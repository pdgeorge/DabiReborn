"""
dabi-stream-brain/handlers/channel_point.py
-------------------------------------------
Handles channel.channel_points_custom_reward_redemption.add events from
Twitch. Only reacts to the reward whose title matches DABI_REDEEM_TITLE
(default: "Ask Dabi a question") — other redemptions are ignored here
(the overlay_controller handles e.g. "daily login bonus" separately).
"""

import logging
import os

LOGGER = logging.getLogger(__name__)

REDEEM_TITLE = os.getenv("DABI_REDEEM_TITLE", "Ask Dabi a question")


def handle(payload: dict, services: object) -> str | None:
    """
    Extract the redemption, ask Dabi, return the response text.
    Returns None if this isn't Dabi's reward.
    """
    event = payload.get("event", {})
    reward = event.get("reward") or {}
    title = str(reward.get("title") or "").strip()

    if title.lower() != REDEEM_TITLE.lower():
        return None

    username = event.get("user_name") or event.get("user_login") or "someone"
    user_input = str(event.get("user_input") or "").strip()

    if user_input:
        prompt = f"{username} asks: {user_input}"
    else:
        prompt = (
            f"{username} redeemed '{title}' but forgot to actually write a "
            f"question. Call them out for it, lovingly."
        )

    LOGGER.info("Redeem '%s' from %s: %s", title, username, user_input[:80])

    response = services.llm.chat(prompt)

    LOGGER.info("Dabi responds: %s", response)
    return response
