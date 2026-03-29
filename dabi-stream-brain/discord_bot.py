"""
discord_bot.py
--------------
Dabi's Discord presence.

- Receives messages in the configured channel → publishes dabi.discord.message to RabbitMQ
- Consumes dabi.discord.response from RabbitMQ → sends text reply to channel
- Consumes dabi.tts.ready from RabbitMQ → plays TTS in voice (when voice works)

RabbitMQ:
  Inbound:  dabi_events (fanout)
            - dabi.tts.ready        → {"text": "..."}
            - dabi.discord.response → {"text": "..."}
  Outbound: dabi_events (fanout)
            - dabi.discord.message  → {"text": "...", "username": "...", "images": [...]}

Slash commands:
  /join          — join the voice channel of the user (role gated)
  /leave         — leave voice channel (role gated)
  /fix_voice     — force reset voice state then rejoin
  /ping          — sanity check
  /test          — debug voice state info
  /queue_length  — how many TTS items are queued

.env keys:
  DISCORD_TOKEN
  DISCORD_GUILD_ID
  RABBITMQ_URL
  DABI_EXCHANGE               (default: dabi_events)
  DISCORD_REQUIRED_ROLE       (default: TheGuyInChargeIGuess)
  DISCORD_LISTEN_CHANNEL      (default: dabi-test)
  MAX_ATTACHMENT_BYTES        (default: 8000000 = 8MB)
  SIZE_EXCEEDED_MESSAGE       (default: built-in Dabi response)
  MAX_GIF_FRAMES              (default: 8)
"""

import asyncio
import base64
import io
import json
import logging
import os
import queue
import sys
from pathlib import Path

import aio_pika
import discord
from discord.ext import commands
from dotenv import load_dotenv
from PIL import Image

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
DISCORD_TOKEN         = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID      = int(os.getenv("DISCORD_GUILD_ID", 0))
RABBITMQ_URL          = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
DABI_EXCHANGE         = os.getenv("DABI_EXCHANGE", "dabi_events")
REQUIRED_ROLE         = os.getenv("DISCORD_REQUIRED_ROLE", "TheGuyInChargeIGuess")
LISTEN_CHANNEL        = os.getenv("DISCORD_LISTEN_CHANNEL", "dabi-test")
MAX_ATTACHMENT_BYTES  = int(os.getenv("MAX_ATTACHMENT_BYTES", 8_000_000))
MAX_GIF_FRAMES        = int(os.getenv("MAX_GIF_FRAMES", 8))
SIZE_EXCEEDED_MESSAGE = os.getenv(
    "SIZE_EXCEEDED_MESSAGE",
    "That image is far too large for my refined sensibilities. Perhaps something smaller next time."
)

TMP_DIR = Path("./tmp")
TMP_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------
intents = discord.Intents.all()
intents.message_content = True
intents.voice_states = True

bot = discord.Bot(intents=intents)

tts = TTSService()
audio_queue: queue.Queue = queue.Queue()


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------
def _detect_media_type(image_bytes: bytes) -> str:
    """Detect image media type from magic bytes rather than trusting Discord."""
    if image_bytes[:8] == b'\x89PNG\r\n\x1a\n':
        return "image/png"
    elif image_bytes[:3] == b'\xff\xd8\xff':
        return "image/jpeg"
    elif image_bytes[:6] in (b'GIF87a', b'GIF89a'):
        return "image/gif"
    elif image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/png"  # fallback


def _extract_gif_frames(gif_bytes: bytes) -> list[dict]:
    """
    Extract up to MAX_GIF_FRAMES frames from a GIF.
    Always includes first and last frame, with evenly spaced frames in between.
    Returns a list of image dicts ready to pass to LLMService.
    """
    try:
        gif = Image.open(io.BytesIO(gif_bytes))
        frames = []
        try:
            while True:
                frames.append(gif.copy().convert("RGB"))
                gif.seek(gif.tell() + 1)
        except EOFError:
            pass

        total = len(frames)
        if total <= MAX_GIF_FRAMES:
            selected = frames
        else:
            # Always include first and last, evenly space the rest
            indices = [0]
            step = (total - 1) / (MAX_GIF_FRAMES - 1)
            for i in range(1, MAX_GIF_FRAMES - 1):
                indices.append(round(i * step))
            indices.append(total - 1)
            selected = [frames[i] for i in sorted(set(indices))]

        LOGGER.info("GIF: %d total frames, sending %d", total, len(selected))

        result = []
        for frame in selected:
            buf = io.BytesIO()
            frame.save(buf, format="PNG")
            result.append({
                "data": base64.b64encode(buf.getvalue()).decode("utf-8"),
                "media_type": "image/png",
            })
        return result

    except Exception as e:
        LOGGER.error("Failed to extract GIF frames: %s", e)
        return []


# ---------------------------------------------------------------------------
# Voice helpers
# ---------------------------------------------------------------------------
async def hard_reset_voice_state(guild: discord.Guild) -> None:
    try:
        await guild.change_voice_state(channel=None, self_mute=False, self_deaf=False)
    except Exception as e:
        LOGGER.warning("hard_reset_voice_state: %s", e)
    await asyncio.sleep(0.6)


async def play_tts(text: str) -> None:
    """Generate TTS for text and queue it for playback."""
    try:
        path, _ = await tts.generate(text)
        if path:
            audio_queue.put(path)
            LOGGER.info("Queued TTS audio: %s", path)
    except Exception as e:
        LOGGER.error("TTS generation failed: %s", e)


# ---------------------------------------------------------------------------
# RabbitMQ
# ---------------------------------------------------------------------------
async def _publish_discord_message(username: str, text: str, images: list = None) -> None:
    """Publish a dabi.discord.message event to RabbitMQ."""
    try:
        connection = await aio_pika.connect_robust(RABBITMQ_URL)
        async with connection:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                DABI_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True
            )
            payload = json.dumps({
                "username": username,
                "text": text,
                "images": images or [],
            }).encode()
            message = aio_pika.Message(body=payload, type="dabi.discord.message")
            await exchange.publish(message, routing_key="")
            LOGGER.info("Published dabi.discord.message from %s", username)
    except Exception as e:
        LOGGER.error("Failed to publish discord message: %s", e)


async def _send_discord_response(text: str) -> None:
    """Send Dabi's text response back to the configured listen channel."""
    guild = bot.get_guild(DISCORD_GUILD_ID)
    if not guild:
        LOGGER.error("Guild %s not found", DISCORD_GUILD_ID)
        return

    channel = discord.utils.get(guild.text_channels, name=LISTEN_CHANNEL)
    if not channel:
        LOGGER.error("Channel #%s not found", LISTEN_CHANNEL)
        return

    await channel.send(text)
    LOGGER.info("Sent Discord response to #%s", LISTEN_CHANNEL)


async def _rabbitmq_consumer() -> None:
    """Consume dabi_events — handles dabi.tts.ready and dabi.discord.response."""
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
                q = await channel.declare_queue("dabi_discord_inbound", durable=True)
                await q.bind(exchange)
                LOGGER.info("RabbitMQ consumer ready")

                async with q.iterator() as queue_iter:
                    async for message in queue_iter:
                        async with message.process():
                            event_type = message.type or "unknown"
                            try:
                                payload = json.loads(message.body)
                            except json.JSONDecodeError:
                                LOGGER.warning("Invalid JSON in message body")
                                continue

                            text = payload.get("text", "")
                            if not text:
                                continue

                            if event_type == "dabi.tts.ready":
                                # TODO: play in voice when Discord voice is fixed
                                await play_tts(text)

                            elif event_type == "dabi.discord.response":
                                await _send_discord_response(text)

        except Exception as e:
            LOGGER.error("RabbitMQ consumer error: %s — retrying in 5s", e)
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Audio playback loop (voice — parked until Discord voice is fixed)
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
    LOGGER.info("Listening in #%s", LISTEN_CHANNEL)
    await bot.sync_commands()
    asyncio.create_task(_rabbitmq_consumer())
    asyncio.create_task(_audio_playback_loop())


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if message.channel.name != LISTEN_CHANNEL:
        return

    images = []
    for attachment in message.attachments:
        if not attachment.content_type or not attachment.content_type.startswith("image/"):
            continue

        # Size check before downloading
        if attachment.size > MAX_ATTACHMENT_BYTES:
            LOGGER.warning("Attachment too large: %s (%d bytes)", attachment.filename, attachment.size)
            await message.channel.send(SIZE_EXCEEDED_MESSAGE)
            return

        try:
            image_bytes = await attachment.read()
            media_type = _detect_media_type(image_bytes)

            if media_type == "image/gif":
                gif_frames = _extract_gif_frames(image_bytes)
                images.extend(gif_frames)
            else:
                images.append({
                    "data": base64.b64encode(image_bytes).decode("utf-8"),
                    "media_type": media_type,
                })
        except Exception as e:
            LOGGER.error("Failed to read attachment %s: %s", attachment.filename, e)

    await _publish_discord_message(message.author.display_name, message.content, images)


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------
@bot.slash_command(name="join", description="Dabi joins your voice channel")
@commands.has_any_role(REQUIRED_ROLE)
async def join(ctx: discord.ApplicationContext):
    if not ctx.author.voice:
        await ctx.respond("You aren't in a voice channel!", ephemeral=True)
        return
    vc = await ctx.author.voice.channel.connect()
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
        vc = await ctx.author.voice.channel.connect()
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