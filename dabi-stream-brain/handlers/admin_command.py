"""
dabi-stream-brain/handlers/admin_command.py
-------------------------------------------
Broadcaster/mod-only chat commands for managing Dabi himself. Every
other chat message returns None, so this handler safely owns
channel.chat.message while Dabi doesn't reply to regular chat. If
chat_message.handle is ever re-enabled, fold it in as the fallthrough
after the command lookup (the router allows one handler per event type).

Add a command: write a function taking (event, services) and returning
the text Dabi should say (or None for silence), then register it in
COMMANDS.
"""

import logging

LOGGER = logging.getLogger(__name__)


def _is_authorized(event: dict) -> bool:
    chatter_id = str(event.get("chatter_user_id") or "")
    broadcaster_id = str(event.get("broadcaster_user_id") or "")
    if chatter_id and chatter_id == broadcaster_id:
        return True
    for badge in event.get("badges") or []:
        if isinstance(badge, dict) and badge.get("set_id") in ("broadcaster", "moderator"):
            return True
    return False


def _cmd_dabireset(event: dict, services: object) -> str | None:
    services.llm.reset_history()
    LOGGER.info("Dabi's conversation history wiped via !dabireset")
    # Returned text goes out as dabi.tts.ready, so the wipe confirms itself out loud.
    return "Huh? Where am I? Who are all of you people? ...oh, this seems like a lovely stream, I think I'll stay."


COMMANDS = {
    "!dabireset": _cmd_dabireset,
}


def handle(payload: dict, services: object) -> str | None:
    """
    Dispatch admin chat commands; None for anything that isn't one.
    """
    event = payload.get("event", {})
    message = str(event.get("message", {}).get("text") or "").strip()
    if not message:
        return None

    command = COMMANDS.get(message.split()[0].lower())
    if not command:
        return None

    if not _is_authorized(event):
        LOGGER.info(
            "Ignoring %s from unauthorized chatter %s",
            message.split()[0].lower(),
            event.get("chatter_user_login") or event.get("chatter_user_name"),
        )
        return None

    return command(event, services)
