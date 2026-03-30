"""
shared/llm_service.py
---------------------
All LLM interactions. Text in, text out.
Swap models by changing MODEL. Everything else stays the same.

Supports optional image input for vision calls.
Images are passed as base64-encoded bytes with a media type.
"""

import json
import logging
import os
from anthropic import Anthropic

LOGGER = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"


class LLMService:
    def __init__(self, system_json_path: str = "shared/dabi.json", mock: bool = False):
        with open(system_json_path, "r") as f:
            data = json.load(f)

        self.name = data["name"]
        self.voice_service = data["voice_service"]
        self.voice = data["voice"]
        self.system_prompt = data["system"]
        self.mock = mock

        if not self.mock:
            self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        self.history = []

    def chat(self, user_message: str, images: list = None) -> str:
        """
        Send a message, get a response. Maintains conversation history.

        Args:
            user_message: Text to send.
            images: Optional list of dicts with keys:
                    - data: base64-encoded image bytes (str)
                    - media_type: e.g. "image/jpeg", "image/png", "image/gif"

        Returns:
            Response text.
        """
        if self.mock:
            LOGGER.info("[MOCK] chat: %s", user_message)
            return "This is a mock response."

        content = _build_content(user_message, images)
        self.history.append({"role": "user", "content": content})

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system_prompt,
            messages=self.history,
        )

        reply = response.content[0].text

        # Store only text in history — images are not kept between turns
        self.history[-1] = {"role": "user", "content": user_message}
        self.history.append({"role": "assistant", "content": reply})

        return reply

    def single_shot(self, user_message: str, images: list = None) -> str:
        """Send a message with no history. Does not affect conversation state."""
        if self.mock:
            LOGGER.info("[MOCK] single_shot: %s", user_message)
            return "This is a mock single shot response."

        content = _build_content(user_message, images)

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system_prompt,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text

    def reset_history(self) -> None:
        self.history = []


def _build_content(text: str, images: list = None) -> list | str:
    """
    Build the content block for an API call.
    If no images, returns a plain string.
    If images are provided, returns a list of content blocks.
    """
    if not images:
        return text

    content = []
    for image in images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": image["media_type"],
                "data": image["data"],
            },
        })
    content.append({"type": "text", "text": text})
    return content