"""
shared/tts_service.py
---------------------
TTS orchestrator. Text in, audio file path + duration out.
Routes to the correct engine based on the engine parameter.

Engines:
  - streamelements  (default)
  - tiktok
  - edge
  - elevenlabs      (not yet implemented)

Usage:
    from tts_service import TTSService

    tts = TTSService()
    path, duration = tts.generate("Hello!", engine="streamelements", voice="Brian")

    # Or with Dabi's config:
    path, duration = tts.generate(text, engine=dabi.voice_service, voice=dabi.voice)

    # Caller is responsible for deleting the file after playback.
"""

import logging
import os
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)

TTS_OUTPUT_DIR = Path("./tmp/tts")


class TTSService:
    def __init__(self):
        # Lazy imports — only load engines that are actually used
        self._tiktok = None
        self._streamelements = None
        self._edge = None

    def generate(
        self,
        text: str,
        engine: str = "streamelements",
        voice: str = "Brian",
    ) -> tuple[str, int] | tuple[None, None]:
        """
        Generate TTS audio for the given text.

        Args:
            text:   The text to speak.
            engine: TTS engine to use. One of: streamelements, tiktok.
            voice:  Voice identifier for the chosen engine.

        Returns:
            (path, duration_seconds) on success
            (None, None) on failure

        Caller is responsible for deleting the file after playback.
        """
        TTS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = int(time.time() * 1000)
        output_path = str(TTS_OUTPUT_DIR / f"dabi_{timestamp}.mp3")

        LOGGER.info("Generating TTS: engine=%s, voice=%s, chars=%d", engine, voice, len(text))

        if engine == "streamelements":
            return self._generate_streamelements(text, voice, output_path)
        elif engine == "tiktok":
            return self._generate_tiktok(text, voice, output_path)
        elif engine == "edge":
            return self._generate_edge(text, voice, output_path)
        else:
            LOGGER.error("Unknown TTS engine: %s", engine)
            return None, None

    def _generate_streamelements(self, text: str, voice: str, output_path: str):
        if self._streamelements is None:
            from streamelements_tts import streamelements_tts
            self._streamelements = streamelements_tts

        path, duration = self._streamelements(text=text, voice=voice, filename=output_path)
        if path is None:
            LOGGER.error("StreamElements TTS failed for text: %s", text[:50])
            return None, None
        return path, duration

    def _generate_edge(self, text: str, voice: str, output_path: str):
        if self._edge is None:
            from edge_tts import edge_tts
            self._edge = edge_tts

        path, duration = self._edge(text=text, voice=voice, filename=output_path)
        if path is None:
            LOGGER.error("Edge TTS failed for text: %s", text[:50])
            return None, None
        return path, duration

    def _generate_tiktok(self, text: str, voice: str, output_path: str):        session_id = os.getenv("TIKTOK_TOKEN")
        if not session_id:
            LOGGER.error("TIKTOK_TOKEN not set in environment")
            return None, None

        if self._tiktok is None:
            from tiktok_tts import tiktok_tts
            self._tiktok = tiktok_tts

        path, duration = self._tiktok(
            session_id=session_id,
            req_text=text,
            text_speaker=voice,
            filename=output_path,
        )
        if path is None:
            LOGGER.error("TikTok TTS failed for text: %s", text[:50])
            return None, None
        return path, duration
