"""
dabi-stream-brain/handlers/chat_message.py
------------------------------------------
Regular (non-command) chat. Dabi doesn't answer line by line — messages
are buffered by ChatBatch and answered as a group, either once
DABI_CHAT_BATCH_SIZE have piled up or DABI_CHAT_BATCH_WINDOW seconds
after the first one, whichever comes first.

Two halves:
  enqueue()  — called as admin_command's fallthrough, buffers one line
               and always returns None (Dabi stays quiet for now)
  flush()    — called by the flusher task in app.py, drains the buffer
               and produces the line Dabi actually says

Off unless the 'dabichat' redeem (or !dabichat) has turned it on.
"""

import logging

from chat_batch import build_prompt, should_batch

LOGGER = logging.getLogger(__name__)


def enqueue(payload: dict, services: object) -> None:
    """
    Buffer one chat message for the next batch.

    Always returns None — batched replies are published by the flusher
    task, not by this event's response, so there is nothing to say here.
    """
    batch = services.chat_batch
    if not batch.enabled:
        return None

    event = payload.get("event", {})
    if not should_batch(event):
        return None

    username = event.get("chatter_user_name") or event.get("chatter_user_login") or "someone"
    text = event.get("message", {}).get("text", "")

    batch.add(username, text)
    LOGGER.debug("Batched chat from %s (%d pending)", username, batch.pending)
    return None


def flush(services: object) -> str | None:
    """
    Drain the batch and ask Dabi to respond to it.

    Returns the response text, or None if the batch emptied out from
    under us (a redeem preempted between the due check and here).
    """
    messages = services.chat_batch.take()
    if not messages:
        return None

    LOGGER.info("Flushing %d chat messages", len(messages))

    response = services.llm.chat(build_prompt(messages))

    LOGGER.info("Dabi responds to chat: %s", response)
    return response
