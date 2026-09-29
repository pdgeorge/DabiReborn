"""
dabi-stream-brain/router.py
---------------------------
Routes incoming events to the correct handler.
Add new event types here as Dabi gains new reactions.

Response routing:
  - Twitch/hotkey events    → publish to dabi_events as dabi.tts.ready (text)
  - Discord message events  → publish to dabi_events as dabi.discord.response (text)
  - A handler can override its response type by returning (text, event_type)
    — e.g. !goinglive returns dabi.discord.announce
"""

import logging
from handlers import chat_message, discord_message, channel_point, admin_command, stream_online, channel_update

LOGGER = logging.getLogger(__name__)

# Maps event type → (handler, response_event_type)
HANDLERS = {
    # admin_command handles broadcaster/mod !commands (e.g. !dabireset,
    # !dabichat) and falls through to chat_message.enqueue for everything
    # else, which buffers regular chat for a batched reply. Batched replies
    # are published by the flusher task in app.py, not returned from here.
    "channel.chat.message":  (admin_command.handle,   "dabi.tts.ready"),
    "stream.online":         (stream_online.handle,   "dabi.tts.ready"),
    # Silent — only remembers the title/category for !goinglive.
    "channel.update":        (channel_update.handle,  None),
    "dabi.discord.message":  (discord_message.handle, "dabi.discord.response"),
    "channel.channel_points_custom_reward_redemption.add": (channel_point.handle, "dabi.tts.ready"),
    # "channel.subscribe": (subscribe.handle, "dabi.tts.ready"),
    # "channel.follow":    (follow.handle,    "dabi.tts.ready"),
}


def route(event_type: str, payload: dict, services: object) -> tuple[str | None, str | None]:
    """
    Route an event to its handler.

    Returns:
        (response_text, response_event_type) if handled
        (None, None) if unregistered event type
    """
    entry = HANDLERS.get(event_type)
    if not entry:
        return None, None

    handler, response_event_type = entry
    response = handler(payload, services)
    if isinstance(response, tuple):
        return response
    return response, response_event_type