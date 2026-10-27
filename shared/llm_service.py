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
import threading
import time
import requests
from typing import Optional, List, Dict, Any

LOGGER = logging.getLogger(__name__)

# Default model per backend
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

# How much chat history Ollama gets: up to 8 messages, ~1200 tokens
HISTORY_MAX_MESSAGES = 8
HISTORY_MAX_TOKENS = 1200


def _trim_conversation_history(messages: List[Dict[str, str]], max_messages: int = 8, max_tokens: int = 1200) -> List[Dict[str, str]]:
    """
    Trim conversation history to prevent context window explosion on resource-constrained systems.
    Keeps only the most recent messages that fit within token limits.
    
    Args:
        messages: List of message dicts with 'role' and 'content' keys
        max_messages: Maximum number of messages to keep (user + assistant pairs)
        max_tokens: Rough token limit (estimated as chars / 4)
    
    Returns:
        Trimmed list of messages
    """
    if len(messages) <= max_messages:
        # Check if we're still under token limit
        total_chars = sum(len(str(m.get('content', ''))) for m in messages)
        if total_chars <= max_tokens * 4:
            return messages
    
    # Keep only last N messages
    trimmed = messages[-max_messages:]
    
    # Rough token estimation (4 chars ≈ 1 token)
    total_chars = sum(len(str(m.get('content', ''))) for m in trimmed)
    
    # Remove oldest messages until we're under the token limit
    # But always keep at least the last 2 messages (last user + assistant exchange)
    while total_chars > max_tokens * 4 and len(trimmed) > 2:
        trimmed.pop(0)  # Remove oldest
        total_chars = sum(len(str(m.get('content', ''))) for m in trimmed)
    
    LOGGER.debug(f"Trimmed history from {len(messages)} to {len(trimmed)} messages (~{total_chars//4} tokens)")
    return trimmed


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
                
                # Trim history to prevent context window explosion on Pi.
                # _cut_history_when_full already keeps it under the limits between
                # calls, so this only trims when the new message itself is huge.
                trimmed_history = _trim_conversation_history(
                    self.history, max_messages=HISTORY_MAX_MESSAGES, max_tokens=HISTORY_MAX_TOKENS)
                payload = self._ollama_payload(trimmed_history)
                try:
                    resp = requests.post(
                        f"{OLLAMA_BASE_URL}/api/chat",
                        json=payload,
                        timeout=120,
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

        except Exception as e:
            # Roll back — remove the failed user message from history
            self.history.pop()
            LOGGER.error("LLM call failed, history rolled back: %s", e)
            raise

        if self.backend == "ollama":
            self._cut_history_when_full()
        return reply

    def _ollama_payload(self, history: List[Dict[str, str]], num_predict: int = 150) -> Dict[str, Any]:
        """
        chat()'s request to Ollama: the system prompt as a system message, then
        the history. The warm-up sends the same, so it fills the cache chat() reuses.
        """
        messages = []
        if self.system_prompt:
            messages.append({"role": "system", "content": self.system_prompt})
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})
        return {
            "model": OLLAMA_MODEL,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "num_predict": num_predict,  # max tokens equivalent
                "num_ctx": 2048,     # Smaller context window for Pi performance
            },
            "keep_alive": -1  # Keep model loaded in memory
        }

    def _cut_history_when_full(self) -> None:
        """
        Once the history is full, cut it back to the last exchange in one go,
        and warm Ollama up on what's left in the background.

        Ollama only re-reads the part of a prompt that differs from the one
        before. Dropping the oldest message on every call changed the start of
        every prompt, so the Pi re-read the whole history each time: ~38 s a
        reply instead of ~11 s. Cut in one go, the start stays the same until
        the next cut, and the warm-up re-reads it while Dabi is still talking.
        """
        total_chars = sum(len(str(m.get('content', ''))) for m in self.history)
        if len(self.history) < HISTORY_MAX_MESSAGES and total_chars <= HISTORY_MAX_TOKENS * 4:
            return
        LOGGER.info("History full (%d messages), cutting it to the last exchange", len(self.history))
        self.history = self.history[-2:]
        threading.Thread(target=self._warm_up, args=(list(self.history),), daemon=True).start()

    def _warm_up(self, history: List[Dict[str, str]]) -> None:
        """
        Have Ollama read the system prompt and this history now, so the next
        chat() only reads its own message. The placeholder message gets the
        prompt up to where the next one starts; its 1-token reply is ignored.
        """
        started = time.monotonic()
        payload = self._ollama_payload(history + [{"role": "user", "content": "..."}], num_predict=1)
        try:
            resp = requests.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload, timeout=120)
            resp.raise_for_status()
            LOGGER.info("Ollama warm-up done in %.1f s", time.monotonic() - started)
        except requests.exceptions.RequestException as e:
            LOGGER.warning("Ollama warm-up failed, the next reply will be slower: %s", e)

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
                "think": False,
                "options": {
                    "num_predict": 150,
                    "temperature": 0.7,
                    # Must match chat()'s num_ctx: a different context size makes
                    # Ollama reload the whole model (~1 min on the Pi).
                    "num_ctx": 2048,
                    },
                "keep_alive": -1,
            }
            try:
                resp = requests.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json=payload,
                    timeout=120,
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