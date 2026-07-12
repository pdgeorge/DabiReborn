"""
dabi-stream-brain/handlers/stream_online.py
-------------------------------------------
Handles stream.online from Twitch: every stream starts with a fresh
Dabi. Same effect as !dabireset, but automatic — no more remembering
to wipe him manually, and no stale context from three streams ago.
Return None instead of text to make the reset silent.
"""

import logging

LOGGER = logging.getLogger(__name__)


def handle(payload: dict, services: object) -> str | None:
    services.llm.reset_history()
    LOGGER.info("stream.online — Dabi's conversation history reset for the new stream")
    return "Gooooood morning stream! Wait... is it morning? Where am I? Doesn't matter. Let's go!"
