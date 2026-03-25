"""
shared/edge_tts.py
------------------
Edge TTS engine via Microsoft's neural TTS service.
No auth required.

Requires:
    pip install edge-tts

Usage:
    from edge_tts import edge_tts

    path, duration = edge_tts(
        text="Hello I am Dabi!",
        voice="en-GB-RyanNeural",
        filename="output.mp3"
    )

Available voices (selection):
    en-GB-RyanNeural        — British male, characterful
    en-GB-ThomasNeural      — British male, formal
    en-GB-OliverNeural      — British male, warm
    en-US-GuyNeural         — American male, clean
    en-US-ChristopherNeural — American male, deeper
"""

import asyncio
import logging
import subprocess

import edge_tts as _edge_tts

LOGGER = logging.getLogger(__name__)

DEFAULT_VOICE = "en-GB-RyanNeural"


async def edge_tts(
    text: str,
    voice: str = DEFAULT_VOICE,
    filename: str = "voice.mp3",
) -> tuple[str, int] | tuple[None, None]:
    try:
        await _generate(text, voice, filename)
    except Exception as e:
        LOGGER.error("Edge TTS generation failed: %s", e)
        return None, None

    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                filename,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        duration = round(float(result.stdout.strip()))
    except Exception as e:
        LOGGER.warning("Could not determine audio duration: %s", e)
        duration = 0

    LOGGER.info("Edge TTS ready — voice=%s, duration=%ds", voice, duration)
    return filename, duration


async def _generate(text: str, voice: str, filename: str) -> None:
    communicate = _edge_tts.Communicate(text, voice)
    await communicate.save(filename)
