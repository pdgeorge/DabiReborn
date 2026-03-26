"""
discord_bot.py
--------------
Dabi's Discord presence.

- Consumes `dabi.tts.ready` from RabbitMQ → generates TTS → plays in voice
- Publishes `dabi.discord.message` to RabbitMQ when a message is sent in #dabi-talks
- Voice STT via Whisper (py-cord)

RabbitMQ:
  Inbound:  dabi_events (fanout), event type: dabi.tts.ready, payload: {"text": "..."}
  Outbound: dabi_events (fanout), event type: dabi.discord.message, payload: {"text": "...", "username": "..."}

Slash commands:
  /join              — join the voice channel of the user (role gated)
  /leave             — leave voice channel (role gated)
  /fix_voice         — force reset voice state then rejoin
  /ping              — sanity check
  /test              — debug voice state info
  /queue_length      — how many TTS items are queued
  /transcribe        — one-shot STT recording
  /start_listening   — enable continuous STT
  /stop_listening    — disable continuous STT

.env keys:
  DISCORD_TOKEN
  DISCORD_GUILD_ID
  RABBITMQ_URL
  DABI_EXCHANGE           (default: dabi_events)
  DISCORD_REQUIRED_ROLE   (default: TheGuyInChargeIGuess)
  TIKTOK_TOKEN
"""

import asyncio
import json
import logging
import os
import queue
import sys
import tempfile
import threading
from pathlib import Path

import aio_pika
import discord
import torch
import whisper
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "shared"))
from tts_service import TTSService

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("dabi-discord")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DISCORD_TOKEN    = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", 0))
RABBITMQ_URL     = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DABI_EXCHANGE    = os.getenv("DABI_EXCHANGE", "dabi_events")
REQUIRED_ROLE    = os.getenv("DISCORD_REQUIRED_ROLE", "TheGuyInChargeIGuess")

TMP_DIR = Path("./tmp")
TMP_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Whisper STT
# ---------------------------------------------------------------------------
_whisper_model = None

def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        LOGGER.info("Loading Whisper model on %s", device)
        torch.set_num_threads(1)
        _whisper_model = whisper.load_model("base", device=device)
    return _whisper_model

def _transcribe_sync(path: Path) -> str:
    return _get_whisper_model().transcribe(str(path))["text"].strip()

async def transcribe_async(path: Path, timeout: int = 120) -> str:
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, _transcribe_sync, path),
        timeout=timeout,
    )

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.all()
intents.message_content = True
intents.voice_states = True

bot = discord.Bot(intents=intents)

tts = TTSService()
audio_queue: queue.Queue = queue.Queue()
listening_flag: bool = False

# ---------------------------------------------------------------------------
# Voice helpers
# ---------------------------------------------------------------------------
async def hard_reset_voice_state(guild: discord.Guild) -> None:
    try:
        await guild.change_voice_state(channel=None, self_mute=False, self_deaf=False)
    except Exception as e:
        LOGGER.warning("hard_reset_voice_state: %s", e)
    await asyncio.sleep(0.6)


async def ensure_voice(ctx: discord.ApplicationContext):
    if not ctx.author.voice:
        await ctx.respond("You aren't in a voice channel!", ephemeral=True)
        return None
    try:
        vc = ctx.guild.voice_client
        if not vc or not vc.is_connected():
            await hard_reset_voice_state(ctx.guild)
            vc = await ctx.author.voice.channel.connect(reconnect=False, timeout=30.0)
            await asyncio.sleep(1.0)
        return vc
    except Exception as e:
        LOGGER.error("ensure_voice failed: %s", e)
        await ctx.respond(f"Voice connect failed: `{e}`", ephemeral=True)
        return None


async def play_tts(text: str) -> None:
    """Generate TTS for text and queue it for playback."""
    try:
        path, _ = tts.generate(text)
        if path:
            audio_queue.put(path)
            LOGGER.info("Queued TTS audio: %s", path)
    except Exception as e:
        LOGGER.error("TTS generation failed: %s", e)


async def do_transcribe(ctx: discord.ApplicationContext, seconds: int) -> None:
    """Record voice channel audio and publish transcription to RabbitMQ."""
    if not ctx.author.voice:
        await ctx.respond("You aren't in a voice channel.", ephemeral=True)
        return

    vc = ctx.guild.voice_client
    if not vc or not vc.is_connected():
        vc = await ctx.author.voice.channel.connect()

    sink = discord.sinks.WaveSink()

    async def on_finish(sink: discord.sinks.Sink, *args):
        for uid, audio in sink.audio_data.items():
            with tempfile.NamedTemporaryFile(delete=False, suffix=".wav", dir=TMP_DIR) as tmp:
                audio.file.seek(0)
                tmp.write(audio.file.read())
                wav_path = Path(tmp.name)

            text = await transcribe_async(wav_path)
            wav_path.unlink(missing_ok=True)

            user = await bot.fetch_user(uid)
            if text:
                LOGGER.info("STT [%s]: %s", user.display_name, text)
                await _publish_discord_message(user.display_name, text)

    vc.start_recording(sink, on_finish)
    await asyncio.sleep(seconds)
    vc.stop_recording()


# ---------------------------------------------------------------------------
# RabbitMQ
# ---------------------------------------------------------------------------
async def _publish_discord_message(username: str, text: str) -> None:
    """Publish a dabi.discord.message event to RabbitMQ."""
    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
            )
            payload = json.dumps({"username": username, "text": text}).encode()
            message = aio_pika.Message(body=payload, type="dabi.discord.message")
            await exchange.publish(message, routing_key="")
            LOGGER.info("Published dabi.discord.message from %s", username)
    except Exception as e:
        LOGGER.error("Failed to publish discord message: %s", e)


async def _rabbitmq_consumer() -> None:
    """Consume dabi.tts.ready events and queue audio for playback."""
    LOGGER.info("Connecting RabbitMQ consumer...")
    while True:
        try:
            connection = await aio_pika.connect_robust(RABBITMQ_URL)
            async with connection:
                channel = await connection.channel()
                await channel.set_qos(prefetch_count=10)
                exchange = await channel.declare_exchange(
                    DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
                )
                q = await channel.declare_queue("dabi_discord_tts", durable=True)
                await q.bind(exchange)
                LOGGER.info("RabbitMQ consumer ready")

                async with q.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            if message.type != "dabi.tts.ready":
                                continue
                            try:
                                payload = json.loads(message.body)
                                text = payload.get("text", "")
                                if text:
                                    await play_tts(text)
                            except json.JSONDecodeError:
                                LOGGER.warning("Invalid JSON in dabi.tts.ready message")

        except Exception as e:
            LOGGER.error("RabbitMQ consumer error: %s — retrying in 5s", e)
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Audio playback loop
# ---------------------------------------------------------------------------
async def _audio_playback_loop() -> None:
    """Drain the audio queue and play files into the voice channel."""
    while True:
        guild = bot.get_guild(DISCORD_GUILD_ID)
        if guild:
            vc = guild.voice_client
            if vc and vc.is_connected() and not vc.is_playing() and not audio_queue.empty():
                path = audio_queue.get()
                try:
                    LOGGER.info("Playing audio: %s", path)
                    source = discord.FFmpegPCMAudio(path)
                    def after(e):
                        if e:
                            LOGGER.error("Playback error: %s", e)
                        try:
                            os.unlink(path)
                        except OSError:
                            pass
                    vc.play(source, after=after)
                except Exception as e:
                    LOGGER.error("Failed to play audio: %s", e)
        await asyncio.sleep(0.2)


# ---------------------------------------------------------------------------
# Bot events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    LOGGER.info("Logged in as %s (%s)", bot.user, bot.user.id)
    await bot.sync_commands()
    asyncio.create_task(_rabbitmq_consumer())
    asyncio.create_task(_audio_playback_loop())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.name == "dabi-talks":
        await _publish_discord_message(message.author.display_name, message.content)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.slash_command(name="join", description="Dabi joins your voice channel")
@commands.has_any_role(REQUIRED_ROLE)
async def join(ctx: discord.ApplicationContext):
    vc = await ensure_voice(ctx)
    if vc:
        await ctx.respond(f"Joined **{vc.channel.name}**!")


@bot.slash_command(name="leave", description="Dabi leaves the voice channel")
@commands.has_any_role(REQUIRED_ROLE)
async def leave(ctx: discord.ApplicationContext):
    if not ctx.guild.voice_client or not ctx.guild.voice_client.is_connected():
        await ctx.respond("I'm not in a voice channel.")
        return
    await ctx.guild.voice_client.disconnect()
    await ctx.respond("Left the voice channel.")


@bot.slash_command(name="fix_voice", description="Force reset voice state then rejoin")
@commands.has_any_role(REQUIRED_ROLE)
async def fix_voice(ctx: discord.ApplicationContext):
    await ctx.respond("Resetting voice state...")
    await hard_reset_voice_state(ctx.guild)
    await asyncio.sleep(0.5)
    if ctx.author.voice:
        vc = await ensure_voice(ctx)
        if vc:
            await ctx.followup.send("Reconnected ✅")
        else:
            await ctx.followup.send("Still failing ❌")


@bot.slash_command(name="ping", description="Check if Dabi is awake")
async def ping(ctx: discord.ApplicationContext):
    await ctx.respond("pong")


@bot.slash_command(name="test", description="Debug voice state info")
async def test(ctx: discord.ApplicationContext):
    await ctx.respond(
        f"author.voice={ctx.author.voice}\nguild.voice_client={ctx.guild.voice_client}",
        ephemeral=True,
    )


@bot.slash_command(name="queue_length", description="How many TTS items are queued?")
async def queue_length_cmd(ctx: discord.ApplicationContext):
    await ctx.respond(f"There are **{audio_queue.qsize()}** items in the audio queue.")


@bot.slash_command(name="transcribe", description="Record and transcribe voice channel")
@commands.has_any_role(REQUIRED_ROLE)
async def transcribe(
    ctx: discord.ApplicationContext,
    seconds: discord.Option(int, "Seconds to record (1-120)", default=10, min_value=1, max_value=120),  # type: ignore
):
    await ctx.respond(f"Recording for **{seconds}s**...")
    await do_transcribe(ctx, seconds)


@bot.slash_command(name="start_listening", description="Enable continuous STT")
@commands.has_any_role(REQUIRED_ROLE)
async def start_listening(ctx: discord.ApplicationContext):
    global listening_flag
    listening_flag = True
    await ctx.respond("Now listening and transcribing.")


@bot.slash_command(name="stop_listening", description="Disable continuous STT")
@commands.has_any_role(REQUIRED_ROLE)
async def stop_listening(ctx: discord.ApplicationContext):
    global listening_flag
    listening_flag = False
    await ctx.respond("Stopped listening.")


# ---------------------------------------------------------------------------
# TODO: Dabispirations
# Send an image to a specific Discord channel.
# Previously: ask Dabi to inspire you → search Google Images → pick from top 10
# → overlay 3-5 word caption → post to #dabispirations + OBS popup.
# Needs: Google image search, image compositing, _send_image_to_discord()
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def start_bot() -> None:
    LOGGER.info("Starting Dabi Discord bot...")
    bot.run(DISCORD_TOKEN)


if __name__ == "__main__":
    start_bot()
