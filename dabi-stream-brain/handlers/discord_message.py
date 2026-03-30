"""
dabi-stream-brain/handlers/discord_message.py
---------------------------------------------
Handles dabi.discord.message events — messages sent in the configured
Discord channel. Calls LLMService and publishes dabi.discord.response.

Supports text-only and text+image messages.
If multiple images are attached, only the first is sent to Claude.
Dabi is informed if additional images were ignored.
"""

import logging
import os

LOGGER = logging.getLogger(__name__)

IMAGE_PROMPT = os.getenv(
    "DISCORD_IMAGE_PROMPT",
    "I need you to react to this image! Opinions? Funny? Interesting? Give me your best take!"
)

TOO_MANY_IMAGES_NOTE = (
    " Also, there were more images attached but I can only look at one thing at a time: "
    "tell them not to flood me with pictures!"
)


def handle(payload: dict, services: object) -> str | None:
    """
    Extract the Discord message and optional images, ask Dabi, return response.
    Returns None if the message should be ignored.
    """
    username = payload.get("username", "someone")
    text = payload.get("text", "").strip()
    images = payload.get("images", [])

    has_images = bool(images)
    has_text = bool(text)

    if not has_text and not has_images:
        return None

    # Build the prompt text
    if has_images:
        image_prefix = IMAGE_PROMPT + " "
        prompt_text = f"{username} says: {image_prefix}{text}" if has_text else f"{username} says: {image_prefix}"
        if len(images) > 1:
            prompt_text += TOO_MANY_IMAGES_NOTE
        # Only send the first image
        images_to_send = [images[0]]
    else:
        prompt_text = f"{username} says: {text}"
        images_to_send = None

    LOGGER.info(
        "Discord message from %s: %s [images: %d]",
        username, text[:50], len(images)
    )

    response = services.llm.chat(prompt_text, images=images_to_send)

    LOGGER.info("Dabi responds: %s", response)
    return response