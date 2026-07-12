"""
dabi-stream-brain/router.py
---------------------------
Routes incoming events to the correct handler.
Add new event types here as Dabi gains new reactions.

Response routing:
  - Twitch/hotkey events    → publish to dabi_events as dabi.tts.ready (text)
  - Discord message events  → publish to dabi_events as dabi.discord.response (text)
"""

import logging
from handlers import chat_message, discord_message, channel_point, admin_command, stream_online

LOGGER = logging.getLogger(__name__)

# Maps event type → (handler, response_event_type)
HANDLERS = {
    # admin_command only reacts to broadcaster/mod !commands (e.g. !dabireset);
    # regular chat is ignored. If chat_message.handle is re-enabled, fold it in
    # as admin_command's fallthrough — one handler per event type.
    "channel.chat.message":  (admin_command.handle,   "dabi.tts.ready"),
    "stream.online":         (stream_online.handle,   "dabi.tts.ready"),
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
    response_text = handler(payload, services)
    return response_text, response_event_type