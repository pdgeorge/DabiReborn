"""
shared/llm_service.py
--------------------
All LLM interactions. Text in, text out.
Swap backends via LLM_BACKEND environment variable (anthropic, ollama, mock).

Supports optional image input for vision calls.
Images are passed as base64-encoded bytes with a media type.
History always stores text only — images are single-use context, never persisted.

Backends: anthropic (default), ollama, mock
"""

import json
import logging
import os
import requests
from typing import Optional, List, Dict, Any

LOGGER = logging.getLogger(__name__)

# Default model per backend
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")


class LLMService:
    def __init__(self, system_json_path: str = "shared/dabi.json", mock: bool = False):
        with open(system_json_path, "r") as f:
            data = json.load(f)

        self.name = data["name"]
        self.voice_service = data["voice_service"]
        self.voice = data["voice"]
        self.system_prompt = data["system"]
        self.mock = mock
        self.history = []

        if self.mock:
            self.backend = "mock"
            LOGGER.info("LLMService initialized with mock backend")
            return

        self.backend = os.getenv("LLM_BACKEND", "anthropic").lower()
        if self.backend == "anthropic":
            from anthropic import Anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY environment variable is required for anthropic backend")
            self.client = Anthropic(api_key=api_key)
            LOGGER.info("LLMService initialized with Anthropic backend")
        elif self.backend == "ollama":
            # No client object needed, using requests
            self.client = None
            LOGGER.info(f"LLMService initialized with Ollama backend (model={OLLAMA_MODEL}, base_url={OLLAMA_BASE_URL})")
        else:
            raise ValueError(f"Unknown LLM_BACKEND: {self.backend}")

    def chat(self, user_message: str, images: list = None) -> str:
        """
        Send a message, get a response. Maintains conversation history.
        History always stores text only — images are single-use context, never stored.

        Args:
            user_message: Text to send.
            images: Optional list of dicts with keys:
                    - data: base64-encoded image bytes (str)
                    - media_type: e.g. "image/jpeg", "image/png", "image/gif"

        Returns:
            Response text.

        Raises:
            Exception: Re-raises any API error after rolling back history.
        """
        if self.mock:
            LOGGER.info("[MOCK] chat: %s", user_message)
            return "This is a mock response."

        # Always append text-only to history first
        self.history.append({"role": "user", "content": user_message})

        try:
            if self.backend == "anthropic":
                # Build content with images for the API call only — not stored in history
                content = _build_content(user_message, images)
                messages_to_send = self.history[:-1] + [{"role": "user", "content": content}]

                response = self.client.messages.create(
                    model=ANTHROPIC_MODEL,
                    max_tokens=300,
                    system=self.system_prompt,
                    messages=messages_to_send,
                )

                reply = response.content[0].text

            elif self.backend == "ollama":
                if images:
                    LOGGER.warning("Ollama backend does not yet support images, ignoring")
                    # TODO: Revisit later. When we add a second image-to-text model
                # Build messages for Ollama: system prompt as a system message, then history
                messages = []
                if self.system_prompt:
                    messages.append({"role": "system", "content": self.system_prompt})
                # history already includes the new user message at the end
                for msg in self.history:
                    messages.append({"role": msg["role"], "content": msg["content"]})
                # Ensure the last message is the user message (already there)
                payload = {
                    "model": OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": {"num_predict": 300}  # max tokens equivalent
                }
                try:
                    resp = requests.post(
                        f"{OLLAMA_BASE_URL}/api/chat",
                        json=payload,
                        timeout=60,
                    )
                    resp.raise_for_status()
                    result = resp.json()
                    reply = result["message"]["content"]
                except requests.exceptions.RequestException as e:
                    LOGGER.error("Ollama request failed: %s", e)
                    raise

            else:
                raise ValueError(f"Unsupported backend: {self.backend}")

            self.history.append({"role": "assistant", "content": reply})
            return reply

        except Exception as e:
            # Roll back — remove the failed user message from history
            self.history.pop()
            LOGGER.error("LLM call failed, history rolled back: %s", e)
            raise

    def single_shot(self, user_message: str, images: list = None) -> str:
        """Send a message with no history. Does not affect conversation state."""
        if self.mock:
            LOGGER.info("[MOCK] single_shot: %s", user_message)
            return "This is a mock single shot response."

        if self.backend == "anthropic":
            content = _build_content(user_message, images)
            response = self.client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=300,
                system=self.system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text

        elif self.backend == "ollama":
            if images:
                LOGGER.warning("Ollama backend does not yet support images, ignoring")
                # TODO: Revisit later. When we add a second image-to-text model
            messages = []
            if self.system_prompt:
                messages.append({"role": "system", "content": self.system_prompt})
            messages.append({"role": "user", "content": user_message})
            payload = {
                "model": OLLAMA_MODEL,
                "messages": messages,
                "stream": False,
                "options": {"num_predict": 300}
            }
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                    timeout=60,
                )
                resp.raise_for_status()
                result = resp.json()
                return result["message"]["content"]
            except requests.exceptions.RequestException as e:
                LOGGER.error("Ollama request failed: %s", e)
                raise

        else:
            raise ValueError(f"Unsupported backend: {self.backend}")

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