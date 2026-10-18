"""
ptt_client/ptt.py
-----------------
Push-to-talk for Stream Dabi. Runs on the streaming PC, not the Pi.

Press the hotkey, talk, press it again: the clip is transcribed locally
with faster-whisper and published to the dabi_events exchange as
dabi.ptt.message {"text": ...}. Only text crosses to the Pi.

KDE Wayland won't let an app grab global keys, so the hotkey is a KDE
global shortcut that runs `ptt.py toggle`, which pokes the running
client over a unix socket. Shortcuts only fire on key press, so it's a
toggle, not hold-to-talk.

Usage (ptt.sh runs these with the venv; bare ./ptt.sh = run):
    python ptt.py run       # the client — leave it running while streaming
    python ptt.py toggle    # start recording, or stop and send
    python ptt.py start | stop | cancel

KDE: System Settings → Shortcuts → Add New → Command or Script:
    /path/to/ptt_client/venv/bin/python /path/to/ptt_client/ptt.py toggle

Config (env or ptt_client/.env):
    RABBITMQ_URL          amqp://user:pass@<pi>:5672/
    DABI_EXCHANGE         default dabi_events
    PTT_WHISPER_MODEL     default small.en
    PTT_WHISPER_DEVICE    cpu (default) or cuda
    PTT_WHISPER_COMPUTE   default int8
    PTT_HOTWORDS          names Whisper should expect, comma-separated
                          (without them "Dabi" comes out as "Darby")
    PTT_INPUT_DEVICE      PortAudio input name or index; default = system default
    PTT_MAX_SECONDS       auto-stop after this long, default 60
"""

import asyncio
import json
import logging
import os
import signal
import sys
import threading
import time
import unicodedata

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dabi-ptt")
# The Whisper model download/check logs every HTTP request at INFO.
for noisy in ("httpx", "httpx2", "huggingface_hub"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DABI_EXCHANGE = os.getenv("DABI_EXCHANGE", "dabi_events")
EVENT_TYPE = "dabi.ptt.message"

WHISPER_MODEL = os.getenv("PTT_WHISPER_MODEL", "small.en")
WHISPER_DEVICE = os.getenv("PTT_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE = os.getenv("PTT_WHISPER_COMPUTE", "int8")
HOTWORDS = os.getenv("PTT_HOTWORDS", "Dabi, Noita, Teto, pdgeorge")
INPUT_DEVICE = os.getenv("PTT_INPUT_DEVICE") or None
MAX_SECONDS = float(os.getenv("PTT_MAX_SECONDS", "60"))

SAMPLE_RATE = 16000  # what Whisper wants; PipeWire resamples the mic for us
MIN_SECONDS = 0.4    # shorter than this is a double-tap, not speech

SOCKET_PATH = os.path.join(
    os.getenv("XDG_RUNTIME_DIR") or "/tmp", "dabi-ptt.sock"
)
COMMANDS = ("toggle", "start", "stop", "cancel")


def _input_device():
    # Numeric strings are PortAudio indexes, anything else is a name match.
    if INPUT_DEVICE is not None and INPUT_DEVICE.isdigit():
        return int(INPUT_DEVICE)
    return INPUT_DEVICE


def is_usable(text: str) -> bool:
    """Reject the shapes Whisper hallucinates instead of staying quiet.

    Same rules as bob-twitch-chatter's transcriber: punctuation with no
    words, or a decoder that escaped into another script.
    """
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for c in letters if "LATIN" not in unicodedata.name(c, ""))
    return foreign / len(letters) <= 0.2


class Recorder:
    """Mic capture between start() and stop(). PortAudio calls back on its own thread."""

    def __init__(self):
        import sounddevice as sd
        self._sd = sd
        self._stream = None
        self._chunks = []
        self._lock = threading.Lock()
        self.started_at = None

    @property
    def recording(self) -> bool:
        return self._stream is not None

    def _callback(self, indata, frames, time_info, status):
        if status:
            LOGGER.warning("Audio status: %s", status)
        with self._lock:
            self._chunks.append(indata.copy())

    def start(self):
        self._chunks = []
        self._stream = self._sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            device=_input_device(),
            callback=self._callback,
        )
        self._stream.start()
        self.started_at = time.monotonic()

    def stop(self):
        """Stop and return the clip as a mono float32 array (possibly empty)."""
        import numpy as np
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks)[:, 0]


def beep(rising: bool):
    """Two short tones: rising = listening, falling = sent. Best effort."""
    try:
        import numpy as np
        import sounddevice as sd
        tones = (660, 990) if rising else (990, 660)
        t = np.arange(int(0.07 * 22050)) / 22050
        fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)
        clip = np.concatenate([0.2 * np.sin(2 * np.pi * f * t) * fade for f in tones])
        sd.play(clip.astype(np.float32), 22050)
    except Exception as e:
        LOGGER.debug("Beep failed: %s", e)


class PTTClient:
    def __init__(self):
        from faster_whisper import WhisperModel
        LOGGER.info("Loading Whisper %s on %s (%s)…", WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE)
        self.model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE)
        self.recorder = Recorder()
        self.exchange = None
        self._auto_stop = None

    # --- RabbitMQ -------------------------------------------------------

    async def connect(self, aio_pika):
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        channel = await connection.channel()
        self.exchange = await channel.declare_exchange(
            DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
        )
        LOGGER.info("Connected to RabbitMQ, publishing to %s", DABI_EXCHANGE)
        return connection

    async def publish(self, text: str):
        import aio_pika
        message = aio_pika.Message(body=json.dumps({"text": text}).encode(), type=EVENT_TYPE)
        await self.exchange.publish(message, routing_key="")
        LOGGER.info("Published %s: %s", EVENT_TYPE, text)

    # --- Commands -------------------------------------------------------

    async def command(self, cmd: str) -> str:
        if cmd == "toggle":
            cmd = "stop" if self.recorder.recording else "start"

        if cmd == "start":
            if self.recorder.recording:
                return "already recording"
            self.recorder.start()
            beep(rising=True)
            self._auto_stop = asyncio.get_running_loop().call_later(
                MAX_SECONDS, lambda: asyncio.ensure_future(self.command("stop"))
            )
            LOGGER.info("Listening…")
            return "recording"

        if cmd in ("stop", "cancel"):
            if not self.recorder.recording:
                return "not recording"
            if self._auto_stop:
                self._auto_stop.cancel()
                self._auto_stop = None
            audio = self.recorder.stop()
            beep(rising=False)
            if cmd == "cancel":
                LOGGER.info("Cancelled")
                return "cancelled"
            # Transcribe in the background so the socket answers straight away
            # and a quick next press can start a new clip.
            asyncio.ensure_future(self._send(audio))
            return "sending"

        return f"unknown command: {cmd}"

    async def _send(self, audio):
        seconds = len(audio) / SAMPLE_RATE
        if seconds < MIN_SECONDS:
            LOGGER.info("Clip too short (%.2fs), ignored", seconds)
            return
        started = time.monotonic()
        try:
            text = await asyncio.to_thread(self._transcribe, audio)
        except Exception as e:
            LOGGER.error("Transcription failed: %s", e)
            return
        LOGGER.info("Transcribed %.1fs of audio in %.2fs", seconds, time.monotonic() - started)
        if not is_usable(text):
            LOGGER.info("Nothing usable heard (%r), not sent", text)
            return
        try:
            await self.publish(text)
        except Exception as e:
            LOGGER.error("Publish failed: %s", e)

    def _transcribe(self, audio) -> str:
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            vad_filter=True,
            condition_on_previous_text=False,
            no_repeat_ngram_size=3,
            hotwords=HOTWORDS or None,
        )
        # Whisper's own silence rule: a segment it thinks is probably not
        # speech AND isn't confident about is noise it talked itself into.
        return " ".join(
            segment.text for segment in segments
            if not (segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0)
        ).strip()

    # --- Socket ---------------------------------------------------------

    async def _handle_socket(self, reader, writer):
        try:
            cmd = (await reader.readline()).decode().strip()
            reply = await self.command(cmd)
            writer.write((reply + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    async def run(self):
        import aio_pika
        connection = await self.connect(aio_pika)
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        server = await asyncio.start_unix_server(self._handle_socket, path=SOCKET_PATH)
        os.chmod(SOCKET_PATH, 0o600)
        LOGGER.info("Ready. Toggle with: %s %s toggle", sys.executable, os.path.abspath(__file__))
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        try:
            async with server:
                await stop.wait()
            LOGGER.info("Shutting down")
        finally:
            if self.recorder.recording:
                self.recorder.stop()
            await connection.close()
            if os.path.exists(SOCKET_PATH):
                os.unlink(SOCKET_PATH)


def send_command(cmd: str) -> int:
    """Poke the running client. Kept dependency-free so the hotkey is quick."""
    import socket
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(5)
            sock.connect(SOCKET_PATH)
            sock.sendall((cmd + "\n").encode())
            print(sock.recv(256).decode().strip())
        return 0
    except (FileNotFoundError, ConnectionRefusedError):
        print("dabi-ptt isn't running (start it with: ptt.sh)", file=sys.stderr)
        return 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "run":
        asyncio.run(PTTClient().run())
        return 0
    if cmd in COMMANDS:
        return send_command(cmd)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
