"""
dabi-stream-brain/handlers/noita.py
-----------------------------------
Handles dabi.noita.death, which noita-dabi-mod's bridge sends from the
streaming PC when Pdgeorge dies in Noita. Dabi says what got them and one
thing about tonight's deaths ("That's the third time tonight Hiisi
Grenadier got you"), then adds one silly line of his own. (Wins aren't
sent: Dabi would only talk over them.)

Death payload: {killer, killer_en, self_kill, cause, polymorphed_to,
polymorphed_to_en, place, playtime, enemies_killed, gold, killed_by,
killed_by_extra}. The game names creatures in Finnish; the bridge has put
them into English already (killer_en, with Pdgeorge's nicknames like "CEO").
killer is "" when nothing killed them (acid, drowning, an explosion with
nobody to blame), and self_kill is true when they killed themselves: the
bridge calls that killer "Yourself", which would sound like Dabi did it.
cause is the game's damage type: "projectile", "explosion", "acid",
"drowning", "turned to gold"... place is the biome, in English ("mines"),
and isn't used: where doesn't matter.

The bridge also counts tonight's deaths (since it started), as numbers:
deaths_tonight (this one included), killer_tonight (how often this killer
got them tonight, 0 for no killer or a self-kill), cause_tonight (the same
for deaths with no killer, by cause), self_kills_in_a_row, run_seconds
(from playtime), and longest_run_tonight (true when this run beat every
other tonight). Any of them can be missing, from an older bridge.

Dabi's own line is one of a few angles picked at random (a tip, picking a
side, a wrong fact...), never the same twice in a row, with cheering kept
to 1 in 20 so it means something.

The off switch is the bridge: when it isn't running, nothing arrives. Like
the shop's, these expire after two minutes on the queue, so a brain that
was down doesn't bring up an old death. Like a redeem, a reaction takes the
priority pass (pending chat is dropped, so it isn't talked over). It's a
single shot, outside Dabi's chat history, so one death's reply doesn't
steer the next one's.
"""

import logging
import random

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


# What got them, as a thing Dabi can side with or know a fact about. A
# killer is already a name ("Steve"); otherwise it's the cause.
_THINGS = {
    "explosion": "the explosion",
    "turned to gold": "the gold",
    "drowning": "the water",
    "worm bite": "the worm",
    "physics hit": "the heavy thing",
    "heavy impact": "the heavy thing",
}

_ORDINALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
_NUMBERS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]


def _count(payload: dict, key: str) -> int:
    try:
        return int(payload.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _seconds(payload: dict) -> float | None:
    try:
        return float(payload["run_seconds"])
    except (KeyError, TypeError, ValueError):
        return None


def _ordinal(n: int) -> str:
    return _ORDINALS[n - 1] if 1 <= n <= len(_ORDINALS) else f"{n}th"


def _number(n: int) -> str:
    return _NUMBERS[n] if 0 <= n < len(_NUMBERS) else str(n)


def _thing(payload: dict) -> str:
    """What got them, to side with or know a wrong fact about: "Steve", "the gold", "the acid". "" if nothing did."""
    if payload.get("self_kill"):
        return ""
    killer = _killer(payload)
    if killer:
        return killer
    cause = _text(payload, "cause")
    return _THINGS.get(cause, f"the {cause}") if cause else ""


def tonight(payload: dict) -> str:
    """
    One thing about tonight's deaths, for Dabi to say after the death:
    "That's the third time tonight Hiisi Grenadier got you." The most
    interesting one wins: a run of self-kills, a repeat killer or cause, a
    very short or record-long run, then just the count. "" if the bridge
    didn't count.
    """
    if payload.get("self_kill"):
        in_a_row = _count(payload, "self_kills_in_a_row")
        if in_a_row >= 2:
            return f"That's {_number(in_a_row)} times in a row you got yourself."
    else:
        killer, cause = _killer(payload), _cause(payload)
        if killer and _count(payload, "killer_tonight") >= 2:
            return f"That's the {_ordinal(_count(payload, 'killer_tonight'))} time tonight {killer} got you."
        if not killer and cause and _count(payload, "cause_tonight") >= 2:
            return f"That's the {_ordinal(_count(payload, 'cause_tonight'))} time tonight you died from {cause}."
    seconds = _seconds(payload)
    if seconds is not None and seconds < 60:
        return "That run lasted less than a minute."
    if payload.get("longest_run_tonight") and seconds is not None and seconds >= 30 * 60:
        return f"That run lasted {playtime_words(payload.get('playtime'))}, the longest tonight."
    deaths = _count(payload, "deaths_tonight")
    if deaths == 1:
        return "That's your first death tonight."
    return f"That's death number {deaths} tonight." if deaths > 1 else ""


# Dabi's own line after the death: (name, weight, task). Tested against
# gemma4:e2b on 7 kinds of death from Pdgeorge's saves. Dropped: pretending
# to be the killer ("I am Steve!"), asking chat (always about clouds),
# predicting the next death (never did), mixed-up sayings (it used the real
# ones). Picking a side only works given both names. Asked to rate a death
# it always says 8 or 9, so the score is picked here.
ANGLES = [
    ("tip", 30, lambda thing, score: "add one short silly tip for next time"),
    ("side", 25, lambda thing, score: f"say in one short sentence whose side you're on, {STREAMER}'s or {thing}'s, with one silly reason"),
    ("fact", 20, lambda thing, score: f"tell them one short, confidently wrong fact about {thing}"),
    ("reason", 15, lambda thing, score: "add one short sentence with your confidently wrong reason why it happened"),
    ("rate", 5, lambda thing, score: f"say in one short sentence that it was a {score} out of ten death, with one silly reason"),
    ("cheer", 5, lambda thing, score: "cheer them on in one short sentence"),
]
_NEEDS_THING = {"side", "fact"}
_last_angle = None


def pick_angle(payload: dict, rng=random) -> str:
    """An angle for Dabi's line, by weight, never the one he used last time."""
    global _last_angle
    thing = _thing(payload)
    choices = [(name, weight) for name, weight, _ in ANGLES
               if name != _last_angle and (thing or name not in _NEEDS_THING)]
    _last_angle = rng.choices([name for name, _ in choices], [weight for _, weight in choices])[0]
    return _last_angle


def death_prompt(payload: dict, angle: str = "tip", score: int = 7) -> str:
    """
    Tested against gemma4:e2b. Without "it's only a game" it mourns them ("I
    hope Pdgeorge is okay"). Asked to "say what got them", it makes it up ("a
    giant fluffy cloud"), so it's given the whole line to say. On a self-kill
    it takes the blame itself ("I was just being helpful") unless told it was
    only watching. Example replies only made it copy their wording.
    """
    said = " ".join(part for part in [what_happened(payload) + "!" if what_happened(payload) else "", tonight(payload)] if part)
    task = dict((name, task) for name, _, task in ANGLES)[angle](_thing(payload), score)
    watching = " It wasn't you, Dabi, you were only watching." if payload.get("self_kill") else ""
    say = f'say "{said}", then {task}' if said else task
    return (
        f"{STREAMER} just died in Noita, the video game they're playing on stream. "
        f"It's only a game, so be silly, not sad.{watching} Talk to {STREAMER}: {say}."
    )


def death_fallback(payload: dict) -> str:
    """What Dabi says when the LLM can't answer."""
    line, fact = what_happened(payload), tonight(payload)
    said = " ".join(part for part in [f"{line}!" if line else "", fact] if part)
    return f"Oh no, {STREAMER}! {said}" if said else f"Oh no, {STREAMER}, you died!"


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


def handle_death(payload: dict, services: object, rng=random) -> str:
    angle = pick_angle(payload, rng)
    LOGGER.info("Noita death: %s %s (killed_by %r%s, angle %s)", what_happened(payload) or "the game didn't say how",
                tonight(payload), payload.get("killed_by"), payload.get("killed_by_extra") or "", angle)
    prompt = death_prompt(payload, angle, rng.randint(1, 10))
    # Priority pass, as for a redeem: the reaction jumps ahead of pending chatter.
    dropped = services.chat_batch.clear()
    if dropped:
        LOGGER.info("Noita death preempted chat batch — dropped %d pending messages", dropped)
    try:
        reply = " ".join(services.llm.single_shot(prompt).split())
    except Exception as e:
        LOGGER.error("Noita death reaction failed, saying the plain one instead: %s", e)
        reply = ""
    if not reply:
        reply = death_fallback(payload)
    LOGGER.info("Dabi reacts to the death: %s", reply)
    return reply
