"""
dabi-stream-brain/router.py
---------------------------
Routes incoming Twitch events to the correct handler.
Add new event types here as Dabi gains new reactions.
"""

import logging
from handlers import chat_message

LOGGER = logging.getLogger(__name__)

HANDLERS = {
    "channel.chat.message": chat_message.handle,
    # "channel.channel_points_custom_reward_redemption.add": channel_point.handle,
    # "channel.subscribe": subscribe.handle,
    # "channel.follow": follow.handle,
}


def route(event_type: str, payload: dict, services: object) -> str | None:
    """
    Route an event to its handler. Returns response text or None.
    Silently ignores unregistered event types.
    """
    handler = HANDLERS.get(event_type)
    if not handler:
        return None
    return handler(payload, services)