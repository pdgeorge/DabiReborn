"""
shared/llm_service.py
---------------------
All LLM interactions. Text in, text out.
Swap models by changing MODEL. Everything else stays the same.
"""

import os
import json
from anthropic import Anthropic

MODEL = "claude-haiku-4-5-20251001"

class LLMService:
    def __init__(self, system_json_path: str = "shared/dabi.json"):
        with open(system_json_path, "r") as f:
            data = json.load(f)
        
        self.name = data["name"]
        self.voice_service = data["voice_service"]
        self.voice = data["voice"]
        self.system_prompt = data["system"]
        self.client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.history = []

    def chat(self, user_message: str) -> str:
        """Send a message, get a response. Maintains conversation history."""
        self.history.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system_prompt,
            messages=self.history,
        )

        reply = response.content[0].text
        self.history.append({"role": "assistant", "content": reply})
        return reply

    def single_shot(self, user_message: str) -> str:
        """Send a message with no history. Does not affect conversation state."""
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=self.system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return response.content[0].text

    def reset_history(self) -> None:
        self.history = []