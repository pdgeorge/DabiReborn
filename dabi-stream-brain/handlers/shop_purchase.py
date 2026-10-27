"""
dabi-stream-brain/handlers/shop_purchase.py
-------------------------------------------
Handles dabi.shop.purchase, which twitch-broadcaster's overlay_controller
sends when someone buys a cosmetic for their Dabling from Dabi's shop with
channel points. Dabi thanks them out loud, by name, with something about
what they bought.

Payload: {user_name, user_login, item, slot, copies, cost}. item is the
cosmetic's id (shirtA, bronze_first), slot where it's worn (torso, head),
and copies how many the buyer has now: the shop sells doubles.

The off switch is on the controller's side: /admin's Shop tab stops it
sending these. They expire after two minutes on the queue, so a purchase
made while this brain was down isn't thanked whenever it next starts.

Like a redeem, a purchase takes the priority pass: pending chat is dropped,
so the thank-you isn't talked over. It goes through Dabi's chat history, so
he can bring up what people bought later on.
"""

import logging
import re

LOGGER = logging.getLogger(__name__)

# Where a slot's cosmetics go, for the prompt.
_WHERE = {"torso": " to wear", "head": " to wear on their head"}


def item_name(item_id: str) -> str:
    """
    How Dabi says an item. Ids are written for code and TTS reads them out:
    item_name("shirtA") -> "shirt A", item_name("bronze_first") -> "bronze first".
    """
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z0-9])", " ", item_id)
    return " ".join(re.split(r"[\s_-]+", spaced)).strip()


def build_prompt(name: str, item: str, slot: str, copies: int) -> str:
    """
    Tested against gemma4:e2b: without "Talk to <name>" it thanks them in the
    third person, without "say <item>" it often leaves the item out, and the
    silly reason is what makes it sound like Dabi.
    """
    if copies > 1:
        return (
            f'{name} paid channel points for another "{item}" from your shop, so now they have {copies}. '
            f'Talk to {name}: thank them, say "{item}", and give one silly reason {copies} is better than one.'
        )
    return (
        f'{name} paid channel points for "{item}" from your shop{_WHERE.get(slot, " to wear")}. '
        f"Talk to {name}: thank them, say \"{item}\", and tell them how good they'll look in it, with one silly reason."
    )


def fallback(name: str, item: str, copies: int) -> str:
    """What Dabi says when the LLM can't answer: plainer, but still a thank-you."""
    if copies > 1:
        return f"Thank you {name}! Another {item}? Now you've got {copies}, so you'll look extra fancy!"
    return f"Thank you {name}! You're going to look amazing in {item}!"


def handle(payload: dict, services: object) -> str | None:
    name = str(payload.get("user_name") or payload.get("user_login") or "").strip()
    item = item_name(str(payload.get("item") or ""))
    if not name or not item:
        LOGGER.warning("Ignoring a shop purchase without a buyer or an item: %s", payload)
        return None
    try:
        copies = max(1, int(payload.get("copies") or 1))
    except (TypeError, ValueError):
        copies = 1

    # Priority pass, as for a redeem: the thank-you jumps ahead of pending chatter.
    dropped = services.chat_batch.clear()
    if dropped:
        LOGGER.info("Shop purchase preempted chat batch — dropped %d pending messages", dropped)

    LOGGER.info("Shop purchase: %s bought %s (has %d)", name, item, copies)
    try:
        reply = " ".join(services.llm.chat(build_prompt(name, item, str(payload.get("slot") or ""), copies)).split())
    except Exception as e:
        LOGGER.error("Shop thank-you failed, saying the plain one instead: %s", e)
        reply = ""
    if not reply:
        reply = fallback(name, item, copies)

    LOGGER.info("Dabi thanks %s: %s", name, reply)
    return reply
