"""
dabi-stream-brain/handlers/admin_command.py
-------------------------------------------
Broadcaster/mod-only chat commands for managing Dabi himself. This
handler owns channel.chat.message (the router allows one handler per
event type), so anything that isn't a command falls through to
chat_message.enqueue for batching.

Add a command: write a function taking (event, services) and returning
the text Dabi should say (or None for silence), then register it in
COMMANDS. Return a (text, event_type) tuple instead to publish somewhere
other than Dabi's voice — !goinglive posts to Discord this way.
"""

import logging

from handlers import chat_message, going_live

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


def _cmd_dabichat(event: dict, services: object) -> str | None:
    """
    Same toggle as the 'dabichat' redeem, for testing without burning
    points. Broadcaster/mod only, like every command here.
    """
    batch = services.chat_batch
    batch.set_enabled(not batch.enabled)
    LOGGER.info("Chat replies %s via !dabichat", "ENABLED" if batch.enabled else "DISABLED")
    return (
        "Fine, fine — I'm listening to you lot now."
        if batch.enabled
        else "Right, tuning chat out. Blissful silence."
    )


COMMANDS = {
    "!dabireset": _cmd_dabireset,
    "!dabichat": _cmd_dabichat,
    "!goinglive": going_live.handle,
}


def handle(payload: dict, services: object) -> str | tuple[str, str] | None:
    """
    Dispatch admin chat commands; None for anything that isn't one.
    """
    event = payload.get("event", {})
    message = str(event.get("message", {}).get("text") or "").strip()
    if not message:
        return None

    command = COMMANDS.get(message.split()[0].lower())
    if not command:
        # Not a command — regular chat, buffer it for the next batch.
        return chat_message.enqueue(payload, services)

    if not _is_authorized(event):
        LOGGER.info(
            "Ignoring %s from unauthorized chatter %s",
            message.split()[0].lower(),
            event.get("chatter_user_login") or event.get("chatter_user_name"),
        )
        return None

    return command(event, services)
