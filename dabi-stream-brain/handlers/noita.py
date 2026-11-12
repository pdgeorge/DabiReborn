"""
dabi-stream-brain/handlers/noita.py
-----------------------------------
Handles dabi.noita.death and dabi.noita.win, which noita-dabi-mod's bridge
sends from the streaming PC when Pdgeorge dies in Noita, or wins. Dabi
reacts out loud: for a death, what got them and one silly tip for next time.

Death payload: {killer, killer_en, self_kill, cause, polymorphed_to,
polymorphed_to_en, place, playtime, enemies_killed, gold, killed_by,
killed_by_extra}. The game names creatures in Finnish; the bridge has put
them into English already (killer_en, with Pdgeorge's nicknames like "CEO").
killer is "" when nothing killed them (acid, drowning, an explosion with
nobody to blame), and self_kill is true when they killed themselves: the
bridge calls that killer "Yourself", which would sound like Dabi did it.
cause is the game's damage type: "projectile", "explosion", "acid",
"drowning", "turned to gold"... place is the biome, in English ("mines").

Win payload: {orbs, playtime, enemies_killed, gold}. playtime is the
game's "7:03:05".

The off switch is the bridge: when it isn't running, nothing arrives. Like
the shop's, these expire after two minutes on the queue, so a brain that
was down doesn't bring up an old death. Like a redeem, a reaction takes the
priority pass (pending chat is dropped, so it isn't talked over), and it goes
through Dabi's chat history, so Dabi can bring it up later.
"""

import logging

LOGGER = logging.getLogger(__name__)

STREAMER = "Pdgeorge"

# Damage types that need a word or two to read as something you die "from"
# or get got "with". The rest read fine as they are: acid, fire, drowning,
# lava, electricity, the Dark Sun...
_CAUSES = {
    "projectile": "a projectile",
    "explosion": "an explosion",
    "melee": "a melee hit",
    "slice": "a slice",
    "kick": "a kick",
    "drill": "a drill",
    "fall": "a fall",
    "worm bite": "a worm bite",
    "plasma beam": "a plasma beam",
    "heavy impact": "a heavy impact",
    "physics hit": "a heavy impact",
    "Supernova": "a supernova",
    "turned to gold": "being turned to gold",
    "holy": "holy damage",
    "toxic": "toxic damage",
}


def _text(payload: dict, key: str) -> str:
    return str(payload.get(key) or "").strip()


def _a(name: str) -> str:
    """"a Flying Sheep", "an Ukko"."""
    return f"an {name}" if name[:1].lower() in "aeiou" else f"a {name}"


def _cause(payload: dict) -> str:
    cause = _text(payload, "cause")
    return _CAUSES.get(cause, cause)


def _killer(payload: dict) -> str:
    return _text(payload, "killer_en") or _text(payload, "killer")


def _form(payload: dict) -> str:
    """What they'd been polymorphed into, or ""."""
    return _text(payload, "polymorphed_to_en") or _text(payload, "polymorphed_to")


def _what_got_you(payload: dict) -> str:
    """
    What happened, said to Pdgeorge: "Hiisi Grenadier got you with an
    explosion", "you died from acid", "you blew yourself up", or "" if
    nobody knows.
    """
    raw_cause, cause, killer = _text(payload, "cause"), _cause(payload), _killer(payload)
    if payload.get("self_kill"):
        if raw_cause == "explosion":
            return "you blew yourself up"
        if raw_cause == "projectile":
            return "you hit yourself with your own spell"
        return f"you got yourself with {cause}" if cause else "you got yourself somehow"
    if killer:
        return f"{killer} got you" + (f" with {cause}" if cause else "")
    return f"you died from {cause}" if cause else ""


def what_happened(payload: dict) -> str:
    """
    The death as a line for Dabi to say: "You died from acid while you were
    a Flying Sheep", "Hiisi Grenadier got you with an explosion". "" if the
    game didn't say.
    """
    how, form = _what_got_you(payload), _form(payload)
    if form:
        how = (how or "you died") + f" while you were {_a(form)}"
    return f"{how[0].upper()}{how[1:]}" if how else ""


def _where(payload: dict) -> str:
    """ " in the mines", " in The Vault", or "". """
    place = _text(payload, "place")
    if not place or "$" in place or place == "unknown":  # "East $0" is a name the game fills in
        return ""
    return f" in {place}" if place.lower().startswith("the ") else f" in the {place}"


def death_prompt(payload: dict) -> str:
    """
    Tested against gemma4:e2b. Without "it's only a game" it mourns them ("I
    hope Pdgeorge is okay"). Asked to "say what got them", it makes it up ("a
    giant fluffy cloud"); told to say "<killer>", it just shouts the name; and
    told to say that "<killer> got them", it sometimes reads the instruction
    out. Given the whole line to say, it says it and adds the tip.
    """
    line = what_happened(payload)
    task = f'say "{line}!", then give them one silly tip for next time' if line else "give them one silly tip for next time"
    return (
        f"{STREAMER} just died{_where(payload)} in Noita, the video game they're playing on stream. "
        f"It's only a game, so be silly, not sad. Talk to {STREAMER}: {task}."
    )


def death_fallback(payload: dict) -> str:
    """What Dabi says when the LLM can't answer."""
    line = what_happened(payload)
    return f"Oh no, {STREAMER}! {line}!" if line else f"Oh no, {STREAMER}, you died!"


def playtime_words(playtime) -> str:
    """The game's "7:03:05" as words: "about 7 hours", "45 minutes". "" if it isn't one."""
    try:
        hours, minutes, _ = (int(part) for part in str(playtime).split(":"))
    except ValueError:
        return ""
    minutes += hours * 60
    if minutes >= 60:
        hours = round(minutes / 60)
        return f"about {hours} hour{'s' if hours != 1 else ''}"
    if minutes >= 1:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    return "less than a minute"


def win_prompt(payload: dict) -> str:
    details = []
    playtime = playtime_words(payload.get("playtime"))
    if playtime:
        details.append(f"after {playtime}")
    try:
        orbs = int(payload.get("orbs") or 0)
    except (TypeError, ValueError):
        orbs = 0
    if orbs > 0:
        details.append(f"with {orbs} orb{'s' if orbs != 1 else ''}")
    said = "".join(f", {detail}" for detail in details)
    return (
        f"{STREAMER} just won Noita, the game they're playing on stream{said}! "
        f"Talk to {STREAMER}: cheer for them, and say one silly way they should celebrate."
    )


def _react(what: str, prompt: str, fallback: str, services: object) -> str:
    # Priority pass, as for a redeem: the reaction jumps ahead of pending chatter.
    dropped = services.chat_batch.clear()
    if dropped:
        LOGGER.info("Noita %s preempted chat batch — dropped %d pending messages", what, dropped)
    try:
        reply = " ".join(services.llm.chat(prompt).split())
    except Exception as e:
        LOGGER.error("Noita %s reaction failed, saying the plain one instead: %s", what, e)
        reply = ""
    if not reply:
        reply = fallback
    LOGGER.info("Dabi reacts to the %s: %s", what, reply)
    return reply


def handle_death(payload: dict, services: object) -> str:
    LOGGER.info("Noita death%s: %s (killed_by %r%s)", _where(payload), what_happened(payload) or "the game didn't say how",
                payload.get("killed_by"), payload.get("killed_by_extra") or "")
    return _react("death", death_prompt(payload), death_fallback(payload), services)


def handle_win(payload: dict, services: object) -> str:
    LOGGER.info("Noita win: %s", payload)
    return _react("win", win_prompt(payload), f"You won Noita, {STREAMER}! I knew you could do it!", services)
