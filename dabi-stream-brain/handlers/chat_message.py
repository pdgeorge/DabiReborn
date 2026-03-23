"""
dabi-stream-brain/handlers/chat_message.py
------------------------------------------
Handles channel.chat.message events from Twitch.
"""

import logging

LOGGER = logging.getLogger(__name__)


def handle(payload: dict, services: object) -> str | None:
    """
    Extract the chat message, ask Dabi, return the response text.
    Returns None if the message should be ignored.
    """
    event = payload.get("event", {})
    username = event.get("chatter_user_name", "someone")
    message = event.get("message", {}).get("text", "")

    if not message:
        return None

    # Ignore messages from the bot itself
    if event.get("chatter_user_id") == event.get("broadcaster_user_id"):
        return None

    LOGGER.info("Chat from %s: %s", username, message)

    prompt = f"{username} says: {message}"
    response = services.llm.chat(prompt)

    LOGGER.info("Dabi responds: %s", response)
    return response