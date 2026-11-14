"""
Tests for Dabi reacting to Noita deaths (handlers/noita.py), with a fake
LLM. Run from dabi-stream-brain: python -m unittest discover tests
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chat_batch import ChatBatch  # noqa: E402
from handlers import noita  # noqa: E402
from router import route  # noqa: E402

# What the bridge sent for the first death the mod caught in the game.
SHEEP_IN_ACID = {
    "killer": "", "killer_en": "", "self_kill": False, "cause": "acid",
    "polymorphed_to": "Lentolammas", "polymorphed_to_en": "Flying Sheep",
    "place": "Overgrown cavern", "playtime": "7:03:05", "enemies_killed": 1293, "gold": 145046,
    "killed_by": " | damage from material: acid", "killed_by_extra": ", while polymorphed to Lentolammas",
}
GRENADIER = {
    **SHEEP_IN_ACID, "killer": "Kranuhiisi", "killer_en": "Hiisi Grenadier", "cause": "explosion",
    "polymorphed_to": "", "polymorphed_to_en": "", "place": "mines",
    "killed_by": "Kranuhiisi | explosion", "killed_by_extra": "",
}
SELF_KILL = {**GRENADIER, "killer": "Minä", "killer_en": "Yourself", "self_kill": True, "killed_by": "Minä | explosion"}
# What the bridge counts on top, for the third Grenadier death of the night.
THIRD_GRENADIER = {**GRENADIER, "deaths_tonight": 9, "killer_tonight": 3, "cause_tonight": 0,
                   "self_kills_in_a_row": 0, "run_seconds": 130.0, "longest_run_tonight": False}


class FakeLLM:
    def __init__(self, reply="Oh no!", fail=False):
        self.reply = reply
        self.fail = fail
        self.prompts = []

    def single_shot(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("ollama is down")
        return self.reply


class Services:
    def __init__(self, llm):
        self.llm = llm
        self.chat_batch = ChatBatch()


class NoitaTest(unittest.TestCase):
    def setUp(self):
        noita._last_angle = None

    def test_death_with_a_killer(self):
        llm = FakeLLM("  Hiisi Grenadier got you with an explosion!\n Next time, wear a big hat.  ")
        text, event_type = route("dabi.noita.death", GRENADIER, Services(llm))
        self.assertEqual(event_type, "dabi.tts.ready")
        self.assertEqual(text, "Hiisi Grenadier got you with an explosion! Next time, wear a big hat.")
        self.assertEqual(len(llm.prompts), 1)
        self.assertTrue(llm.prompts[0].startswith(
            "Pdgeorge just died in Noita, the video game they're playing on stream. "
            "It's only a game, so be silly, not sad. Talk to Pdgeorge: "
            'say "Hiisi Grenadier got you with an explosion!", then '), llm.prompts[0])

    def test_the_whole_prompt(self):
        self.assertEqual(noita.death_prompt(THIRD_GRENADIER, "side"),
            "Pdgeorge just died in Noita, the video game they're playing on stream. "
            "It's only a game, so be silly, not sad. Talk to Pdgeorge: "
            'say "Hiisi Grenadier got you with an explosion! That\'s the third time tonight Hiisi Grenadier got you.", '
            "then say in one short sentence whose side you're on, Pdgeorge's or Hiisi Grenadier's, with one silly reason.")
        self.assertIn("that it was a 3 out of ten death", noita.death_prompt(GRENADIER, "rate", 3))

    def test_nothing_to_blame_while_polymorphed(self):
        self.assertEqual(noita.what_happened(SHEEP_IN_ACID), "You died from acid while you were a Flying Sheep")
        self.assertIn("whose side you're on, Pdgeorge's or the acid's", noita.death_prompt(SHEEP_IN_ACID, "side"))
        self.assertIn("wrong fact about the gold", noita.death_prompt({"cause": "turned to gold"}, "fact"))

    def test_self_kill_never_says_yourself(self):
        # The bridge calls the killer "Yourself", which would sound like Dabi did it.
        self.assertEqual(noita.what_happened(SELF_KILL), "You blew yourself up")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": "projectile"}), "You hit yourself with your own spell")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": "fire"}), "You got yourself with fire")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": ""}), "You got yourself somehow")
        for text in [noita.death_prompt(SELF_KILL), noita.death_fallback(SELF_KILL)]:
            self.assertNotIn("Yourself", text)
        # Without this it blames itself: "I was just being helpful".
        self.assertIn("It wasn't you, Dabi, you were only watching.", noita.death_prompt(SELF_KILL))

    def test_self_kills_get_no_side_or_fact(self):
        # "Pdgeorge's or their own wand's" confused it, so those angles are left out.
        rng = random.Random(0)
        for _ in range(200):
            self.assertNotIn(noita.pick_angle(SELF_KILL, rng), {"side", "fact"})
            self.assertNotIn(noita.pick_angle({}, rng), {"side", "fact"})

    def test_angles(self):
        rng = random.Random(0)
        picked = [noita.pick_angle(GRENADIER, rng) for _ in range(4000)]
        self.assertFalse(any(a == b for a, b in zip(picked, picked[1:])), "same angle twice in a row")
        self.assertEqual(set(picked), {"tip", "side", "fact", "reason", "rate", "cheer"})
        self.assertLess(picked.count("cheer") / len(picked), 0.08)  # about 1 in 20
        self.assertGreater(picked.count("tip"), picked.count("reason"))

    def test_tonight(self):
        def fact(**counts):
            return noita.tonight({**GRENADIER, **counts})
        self.assertEqual(noita.tonight(GRENADIER), "")  # an older bridge doesn't count
        self.assertEqual(fact(deaths_tonight=1), "That's your first death tonight.")
        self.assertEqual(fact(deaths_tonight=8), "That's death number 8 tonight.")
        self.assertEqual(noita.tonight(THIRD_GRENADIER), "That's the third time tonight Hiisi Grenadier got you.")
        self.assertEqual(fact(killer_tonight=14), "That's the 14th time tonight Hiisi Grenadier got you.")
        acid = {**SHEEP_IN_ACID, "deaths_tonight": 5, "cause_tonight": 4}
        self.assertEqual(noita.tonight(acid), "That's the fourth time tonight you died from acid.")
        self.assertEqual(noita.tonight({"cause": "explosion", "cause_tonight": 2}), "That's the second time tonight you died from an explosion.")
        self.assertEqual(noita.tonight({**SELF_KILL, "self_kills_in_a_row": 3}), "That's three times in a row you got yourself.")
        self.assertEqual(noita.tonight({**SELF_KILL, "self_kills_in_a_row": 1, "deaths_tonight": 4}), "That's death number 4 tonight.")
        self.assertEqual(fact(deaths_tonight=6, run_seconds=45), "That run lasted less than a minute.")
        long_run = {"deaths_tonight": 6, "run_seconds": 7 * 3600, "playtime": "7:00:00", "longest_run_tonight": True}
        self.assertEqual(fact(**long_run), "That run lasted about 7 hours, the longest tonight.")
        self.assertEqual(fact(**{**long_run, "run_seconds": 600, "playtime": "0:10:00"}), "That's death number 6 tonight.")
        # A repeat killer beats everything but a self-kill streak.
        self.assertEqual(fact(**{**long_run, "killer_tonight": 2}), "That's the second time tonight Hiisi Grenadier got you.")

    def test_when_the_game_didnt_say_how(self):
        self.assertEqual(noita.what_happened({"polymorphed_to_en": "Fish"}), "You died while you were a Fish")
        self.assertTrue(noita.death_prompt({}).endswith("Talk to Pdgeorge: add one short silly tip for next time."))
        self.assertIn('say "That\'s death number 2 tonight.", then', noita.death_prompt({"deaths_tonight": 2}))

    def test_causes_read_as_words(self):
        for cause, said in [
            ("turned to gold", "You died from being turned to gold"),
            ("drowning", "You died from drowning"),
            ("the Dark Sun", "You died from the Dark Sun"),
            ("melee", "You died from a melee hit"),
            ("", ""),
        ]:
            self.assertEqual(noita.what_happened({"cause": cause}), said)

    def test_names_left_in_finnish(self):
        # names.json keeps Hämis and Ukko as they are, and an unknown name comes through as the game wrote it.
        self.assertEqual(noita.what_happened({"killer": "Hämis", "killer_en": "Hämis", "cause": "melee"}), "Hämis got you with a melee hit")
        self.assertEqual(noita.what_happened({"killer": "Uusiolento"}), "Uusiolento got you")
        self.assertEqual(noita.what_happened({"cause": "fire", "polymorphed_to": "Ukko"}), "You died from fire while you were an Ukko")

    def test_pending_chat_is_dropped(self):
        services = Services(FakeLLM())
        services.chat_batch.set_enabled(True)
        services.chat_batch.add("Lumi", "hi dabi")
        noita.handle_death(GRENADIER, services)
        self.assertEqual(services.chat_batch.pending, 0)

    def test_still_reacts_when_the_llm_fails(self):
        for llm in [FakeLLM(fail=True), FakeLLM("   ")]:
            self.assertEqual(noita.handle_death(SHEEP_IN_ACID, Services(llm)), "Oh no, Pdgeorge! You died from acid while you were a Flying Sheep!")
        failing = Services(FakeLLM(fail=True))
        self.assertEqual(noita.handle_death(THIRD_GRENADIER, failing),
                         "Oh no, Pdgeorge! Hiisi Grenadier got you with an explosion! That's the third time tonight Hiisi Grenadier got you.")
        self.assertEqual(noita.handle_death(SELF_KILL, failing), "Oh no, Pdgeorge! You blew yourself up!")
        self.assertEqual(noita.handle_death({}, failing), "Oh no, Pdgeorge, you died!")

    def test_wins_arent_handled(self):
        self.assertEqual(route("dabi.noita.win", {"orbs": 11}, Services(FakeLLM())), (None, None))

    def test_playtime_as_words(self):
        for playtime, said in [
            ("7:03:05", "about 7 hours"), ("1:00:00", "about 1 hour"), ("0:45:10", "45 minutes"),
            ("0:01:59", "1 minute"), ("0:00:30", "less than a minute"), ("", ""), (None, ""), ("1:2", ""),
        ]:
            self.assertEqual(noita.playtime_words(playtime), said, playtime)


if __name__ == "__main__":
    unittest.main()
