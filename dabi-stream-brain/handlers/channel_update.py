"""
dabi-stream-brain/handlers/channel_update.py
--------------------------------------------
Handles channel.update from Twitch, which fires whenever the stream
title or category changes. Dabi says nothing — this just remembers the
latest values so other handlers (e.g. !goinglive) can mention them.

Memory only: after a restart the title is unknown until it changes again.
"""

import logging

LOGGER = logging.getLogger(__name__)


def handle(payload: dict, services: object) -> None:
    event = payload.get("event", {})
    services.stream_info = {
        "title": str(event.get("title") or "").strip(),
        "category": str(event.get("category_name") or "").strip(),
    }
    LOGGER.info(
        "Stream info updated — title: %r, category: %r",
        services.stream_info["title"], services.stream_info["category"],
    )
    return None
