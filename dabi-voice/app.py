"""
dabi-voice/app.py
-----------------
Dabi's mouth. Consumes dabi.tts.ready from the dabi_events exchange,
generates TTS audio via shared TTSService, runs Rhubarb Lip Sync to get
mouth-shape cues, and pushes it all to browser-source overlays over
WebSocket. The overlay page (served at /) plays the audio inside OBS
and animates the avatar.

Pipeline per event:
  text -> TTSService (engine/voice from dabi.json) -> mp3
       -> ffmpeg -> 16 kHz mono wav
       -> rhubarb --dialogFile <text> -> mouth cues (A-H, X)
       -> broadcast {"type": "dabi.speak", text, audio_url, duration, mouth_cues}

If Rhubarb is missing or fails, mouth_cues is null and the overlay falls
back to amplitude-based mouth flapping — the pipeline still speaks.

HTTP:
  GET  /           -> overlay page (add as OBS browser source)
  GET  /audio/...  -> generated audio files
  POST /say        -> {"text": "..."} manual test endpoint (LAN/tailnet only)
  WS   /ws/voice   -> overlay clients

.env keys:
  RABBITMQ_URL
  DABI_EXCHANGE     (default: dabi_events)
  VOICE_HTTP_PORT   (default: 8090)
  RHUBARB_BIN       (default: rhubarb — set to full path if not on PATH)
  DABI_JSON         (default: shared/dabi.json — name/voice config)
"""

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional, Set

import aio_pika
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# In Docker, shared/ is copied next to app.py; in the repo it lives one level up.
_HERE = os.path.dirname(os.path.abspath(__file__))
SHARED_DIR = next(
    (d for d in (os.path.join(_HERE, "shared"), os.path.join(_HERE, "..", "shared"))
     if os.path.isdir(d)),
    os.path.join(_HERE, "shared"),
)
sys.path.insert(0, SHARED_DIR)
from tts_service import TTSService

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dabi-voice")

RABBITMQ_URL   = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DABI_EXCHANGE  = os.getenv("DABI_EXCHANGE", "dabi_events")
QUEUE_NAME     = "dabi_voice_inbound"
HTTP_PORT      = int(os.getenv("VOICE_HTTP_PORT", "8090"))
RHUBARB_BIN    = os.getenv("RHUBARB_BIN", "rhubarb")
DABI_JSON      = os.getenv("DABI_JSON", os.path.join(SHARED_DIR, "dabi.json"))

BASE_DIR    = Path(__file__).resolve().parent
OVERLAY_DIR = BASE_DIR / "overlay"
WORK_DIR    = BASE_DIR / "tmp" / "voice"       # scratch: wav, dialog, cues
SERVE_DIR   = BASE_DIR / "tmp" / "serve"       # mp3s served at /audio
AUDIO_MAX_AGE_SECONDS = 900

# ---------------------------------------------------------------------------
# Voice config + services
# ---------------------------------------------------------------------------
with open(DABI_JSON) as f:
    _dabi_cfg = json.load(f)
TTS_ENGINE = _dabi_cfg.get("voice_service", "edge")
TTS_VOICE  = _dabi_cfg.get("voice", "en-GB-RyanNeural")

tts = TTSService()

# ---------------------------------------------------------------------------
# WebSocket hub
# ---------------------------------------------------------------------------
connected_clients: Set[WebSocket] = set()


async def broadcast(message: dict) -> None:
    if not connected_clients:
        LOGGER.info("No overlay clients connected — dropping speak event")
        return
    payload = json.dumps(message)
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    connected_clients.difference_update(dead)


# ---------------------------------------------------------------------------
# Pipeline steps
# ---------------------------------------------------------------------------
async def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run a subprocess, return (returncode, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, f"timed out after {timeout}s"
    return proc.returncode, (stderr or b"").decode(errors="replace")


async def generate_mouth_cues(mp3_path: str, text: str, stem: str) -> Optional[list]:
    """
    mp3 -> wav -> rhubarb -> [{"start": s, "end": e, "value": "A".."H"/"X"}, ...]
    Returns None on any failure (overlay falls back to amplitude mode).
    """
    wav_path    = WORK_DIR / f"{stem}.wav"
    dialog_path = WORK_DIR / f"{stem}.txt"
    cues_path   = WORK_DIR / f"{stem}.json"

    try:
        # 16 kHz mono is what Rhubarb's recognizer wants — also faster on a Pi
        code, err = await _run(
            ["ffmpeg", "-y", "-loglevel", "error",
             "-i", mp3_path, "-ar", "16000", "-ac", "1", str(wav_path)],
            timeout=30,
        )
        if code != 0:
            LOGGER.error("ffmpeg failed: %s", err.strip())
            return None

        # We know the exact text — --dialogFile makes the sync far more accurate
        dialog_path.write_text(text)

        code, err = await _run(
            [RHUBARB_BIN, "-f", "json",
             "--dialogFile", str(dialog_path),
             "-o", str(cues_path), str(wav_path)],
            timeout=90,
        )
        if code != 0:
            LOGGER.error("rhubarb failed: %s", err.strip())
            return None

        with open(cues_path) as f:
            data = json.load(f)
        cues = data.get("mouthCues")
        LOGGER.info("Rhubarb produced %d mouth cues", len(cues or []))
        return cues

    except FileNotFoundError as e:
        LOGGER.warning("Lip sync unavailable (%s) — falling back to amplitude", e)
        return None
    except Exception as e:
        LOGGER.error("Mouth cue generation failed: %s", e)
        return None
    finally:
        for p in (wav_path, dialog_path, cues_path):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass


def _cleanup_old_audio() -> None:
    cutoff = time.time() - AUDIO_MAX_AGE_SECONDS
    for p in SERVE_DIR.glob("*.mp3"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


async def speak(text: str) -> Optional[dict]:
    """Full pipeline: text -> audio + cues -> broadcast. Returns the payload."""
    text = (text or "").strip()
    if not text:
        return None

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    SERVE_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup_old_audio()

    LOGGER.info("Speaking (%d chars): %s", len(text), text[:80])

    mp3_path, duration = await tts.generate(text, engine=TTS_ENGINE, voice=TTS_VOICE)
    if not mp3_path:
        LOGGER.error("TTS failed, nothing to speak")
        return None

    stem = Path(mp3_path).stem
    cues = await generate_mouth_cues(mp3_path, text, stem)

    # Move the mp3 into the served directory
    serve_path = SERVE_DIR / f"{stem}.mp3"
    os.replace(mp3_path, serve_path)

    payload = {
        "type": "dabi.speak",
        "id": stem,
        "text": text,
        "audio_url": f"/audio/{serve_path.name}",
        "duration": duration,
        "mouth_cues": cues,
    }
    await broadcast(payload)
    return payload


# ---------------------------------------------------------------------------
# RabbitMQ consumer
# ---------------------------------------------------------------------------
async def _rabbitmq_consumer() -> None:
    backoff = 2
    while True:
        try:
            LOGGER.info("Connecting to RabbitMQ…")
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                # prefetch 1 → lines are processed (and spoken) in order
                await channel.set_qos(prefetch_count=1)
                exchange = await channel.declare_exchange(
                    DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
                )
                queue = await channel.declare_queue(QUEUE_NAME, durable=True)
                await queue.bind(exchange)
                backoff = 2
                LOGGER.info("RabbitMQ connected. Waiting for dabi.tts.ready…")

                async with queue.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            if message.type != "dabi.tts.ready":
                                continue
                            try:
                                data = json.loads(message.body)
                            except json.JSONDecodeError:
                                LOGGER.warning("Invalid JSON in dabi.tts.ready")
                                continue
                            try:
                                await speak(str(data.get("text", "")))
                            except Exception as e:
                                LOGGER.error("speak() failed: %s", e, exc_info=True)

        except Exception as e:
            LOGGER.error("RabbitMQ error: %s. Retrying in %ds…", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.get_event_loop().create_task(_rabbitmq_consumer())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(lifespan=lifespan)


class SayRequest(BaseModel):
    text: str


@app.post("/say")
async def say(req: SayRequest):
    """Manual test endpoint: make Dabi speak without RabbitMQ."""
    payload = await speak(req.text)
    if payload is None:
        return JSONResponse({"ok": False, "error": "TTS failed or empty text"}, status_code=500)
    return {"ok": True, "id": payload["id"], "clients": len(connected_clients)}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "clients": len(connected_clients), "engine": TTS_ENGINE, "voice": TTS_VOICE}


@app.websocket("/ws/voice")
async def voice_ws(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    LOGGER.info("Overlay client connected (%d total)", len(connected_clients))
    try:
        while True:
            await ws.receive_text()  # keepalive pings from the page; content ignored
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)
        LOGGER.info("Overlay client disconnected (%d left)", len(connected_clients))


SERVE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/audio", StaticFiles(directory=str(SERVE_DIR)), name="audio")
app.mount("/", StaticFiles(directory=str(OVERLAY_DIR), html=True), name="overlay")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=HTTP_PORT)
