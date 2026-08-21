"""
dabi-stream-brain/handlers/channel_point.py
-------------------------------------------
Handles channel.channel_points_custom_reward_redemption.add events from
Twitch. Reacts to two rewards, ignores everything else (the
overlay_controller handles e.g. "daily login bonus" separately):

  DABI_REDEEM_TITLE       (default "Ask Dabi a question") — ask Dabi something
  DABI_CHAT_TOGGLE_TITLE  (default "dabichat")            — toggle chat replies

Redeems hold a priority pass: answering one drops any pending chat batch
and resets its timer, so a paid question is never talked over by ambient
chatter that happened to come due at the same moment.
"""

import logging
import os

LOGGER = logging.getLogger(__name__)

REDEEM_TITLE = os.getenv("DABI_REDEEM_TITLE", "Ask Dabi a question")
CHAT_TOGGLE_TITLE = os.getenv("DABI_CHAT_TOGGLE_TITLE", "dabichat")


def handle(payload: dict, services: object) -> str | None:
    """
    Extract the redemption, ask Dabi, return the response text.
    Returns None if this isn't one of Dabi's rewards.
    """
    event = payload.get("event", {})
    reward = event.get("reward") or {}
    title = str(reward.get("title") or "").strip()

    username = event.get("user_name") or event.get("user_login") or "someone"

    if title.lower() == CHAT_TOGGLE_TITLE.lower():
        return _toggle_chat(username, services)

    if title.lower() != REDEEM_TITLE.lower():
        return None

    user_input = str(event.get("user_input") or "").strip()

    # Priority pass: the paid question jumps ahead of pending chatter.
    dropped = services.chat_batch.clear()
    if dropped:
        LOGGER.info("Redeem preempted chat batch — dropped %d pending messages", dropped)

    if user_input:
        prompt = f"{username} paid channel points to ask: {user_input}"
    else:
        prompt = (
            f"{username} redeemed '{title}' but forgot to actually write a "
            f"question. Call them out for it, lovingly."
        )

    LOGGER.info("Redeem '%s' from %s: %s", title, username, user_input[:80])

    response = services.llm.chat(prompt)

    LOGGER.info("Dabi responds: %s", response)
    return response


def _toggle_chat(username: str, services: object) -> str:
    """Flip chat replies on/off and have Dabi announce the change."""
    batch = services.chat_batch
    batch.set_enabled(not batch.enabled)

    LOGGER.info(
        "Chat replies %s via '%s' redeem by %s",
        "ENABLED" if batch.enabled else "DISABLED", CHAT_TOGGLE_TITLE, username,
    )

    if batch.enabled:
        prompt = (
            f"{username} just paid channel points to make you start paying "
            f"attention to chat. Announce that you're now listening to the "
            f"room, in one short sentence."
        )
    else:
        prompt = (
            f"{username} just paid channel points to make you stop paying "
            f"attention to chat. Announce that you're tuning the room out "
            f"again, in one short sentence."
        )

    return services.llm.chat(prompt)
