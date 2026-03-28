"""
dabi-stream-brain/handlers/discord_message.py
---------------------------------------------
Handles dabi.discord.message events — messages sent in the configured
Discord channel. Calls LLMService and publishes dabi.discord.response.
"""

import logging

LOGGER = logging.getLogger(__name__)


def handle(payload: dict, services: object) -> str | None:
    """
    Extract the Discord message, ask Dabi, return the response text.
    Returns None if the message should be ignored.
    """
    username = payload.get("username", "someone")
    text = payload.get("text", "")

    if not text:
        return None

    LOGGER.info("Discord message from %s: %s", username, text)

    prompt = f"{username} says: {text}"
    response = services.llm.chat(prompt)

    LOGGER.info("Dabi responds: %s", response)
    return response