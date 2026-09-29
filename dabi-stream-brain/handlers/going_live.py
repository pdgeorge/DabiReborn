"""
dabi-stream-brain/handlers/going_live.py
----------------------------------------
!goinglive [time] — broadcaster/mod only (gated by admin_command).
Posts a "going live" announcement to Discord:

    @Watcher pdgeorge is going live <t:X:R> (<t:X:t>). <Dabi's commentary>

The ping, the name and the timestamp are fixed text; only the commentary
comes from the LLM, so a small model can't mangle the parts that matter.
The role mention and target channel are added by dabi-discord.

<t:X:R> is Discord's relative timestamp ("in 5 minutes"); <t:X:t> is the
short time ("7:30 pm"). Both render in each reader's own timezone.

Time argument: 5m, 1h, 1h30m, or a bare number of minutes. Defaults to
5 minutes when omitted.
"""

import logging
import os
import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger(__name__)

DEFAULT_MINUTES = 5
# The streamer's local time, for Dabi's commentary (3am streams deserve comment).
LOCAL_TZ = ZoneInfo(os.getenv("DABI_LOCAL_TZ", "Australia/Melbourne"))

_DURATION = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?$")


def _part_of_day(hour: int) -> str:
    # Spelled out because the small model won't notice "3:05 AM" is odd on its own.
    if hour < 6:
        return "the middle of the night — a really unusual hour to be streaming"
    if hour < 12:
        return "the morning"
    if hour < 17:
        return "the afternoon"
    if hour < 21:
        return "the evening"
    return "late at night"


def parse_delay(arg: str | None) -> int | None:
    """
    Seconds until going live, or None if the argument isn't understood.

    parse_delay(None) -> 300, "10" -> 600, "5m" -> 300, "1h" -> 3600,
    "1h30m" -> 5400.
    """
    if not arg:
        return DEFAULT_MINUTES * 60
    arg = arg.strip().lower()
    if arg.isdigit():
        return int(arg) * 60
    match = _DURATION.match(arg)
    if not match or not any(match.groups()):
        return None
    hours, minutes = (int(g) if g else 0 for g in match.groups())
    return hours * 3600 + minutes * 60


def _commentary(services: object, delay: int, live_at: datetime) -> str:
    info = getattr(services, "stream_info", None) or {}
    title = info.get("title")
    category = info.get("category")

    minutes = round(delay / 60)
    lines = [
        "Pdgeorge is about to go live on Twitch and you're announcing it in the Discord.",
        f"The stream starts in about {minutes} minute{'s' if minutes != 1 else ''}, "
        f"at {live_at.strftime('%-I:%M %p')} on a {live_at.strftime('%A')} "
        f"(Pdgeorge's local time, {live_at.strftime('%Z')}) — that's "
        f"{_part_of_day(live_at.hour)}.",
    ]
    if title:
        lines.append(f"The stream title is: {title}")
    if category:
        lines.append(f"The category is: {category}")
    lines.append(
        "Write one or two short, hype sentences of your own commentary to follow "
        "the announcement — riff on the title, the category, the day or the time "
        "of day if any of it is interesting. Don't repeat that he's going live or "
        "when, don't @mention anyone, no hashtags, no quotes around your answer."
    )
    reply = services.llm.single_shot("\n".join(lines))
    # One line, no wrapping quotes — it's appended to the announcement.
    return " ".join(reply.split()).strip('"').strip()


def handle(event: dict, services: object) -> tuple[str, str] | None:
    parts = str(event.get("message", {}).get("text") or "").split()
    delay = parse_delay(parts[1] if len(parts) > 1 else None)
    if delay is None:
        LOGGER.warning("!goinglive: couldn't understand time %r — nothing posted", parts[1])
        return None

    live_ts = int(time.time()) + delay
    live_at = datetime.now(LOCAL_TZ) + timedelta(seconds=delay)
    name = event.get("broadcaster_user_name") or event.get("broadcaster_user_login") or "pdgeorge"

    announcement = f"{name} is going live <t:{live_ts}:R> (<t:{live_ts}:t>)."
    try:
        commentary = _commentary(services, delay, live_at)
    except Exception as e:
        # Still announce — the ping matters more than the flavour text.
        LOGGER.error("!goinglive: commentary failed, posting without it: %s", e)
        commentary = ""
    if commentary:
        announcement = f"{announcement} {commentary}"

    LOGGER.info("!goinglive: %s", announcement)
    return announcement, "dabi.discord.announce"
