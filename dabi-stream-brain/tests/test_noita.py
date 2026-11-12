"""
Tests for Dabi reacting to Noita deaths and wins (handlers/noita.py), with a
fake LLM. Run from dabi-stream-brain: python -m unittest discover tests
"""

import os
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
WIN = {"orbs": 11, "playtime": "3:01:00", "enemies_killed": 500, "gold": 9000}


class FakeLLM:
    def __init__(self, reply="Oh no!", fail=False):
        self.reply = reply
        self.fail = fail
        self.prompts = []

    def chat(self, prompt):
        self.prompts.append(prompt)
        if self.fail:
            raise RuntimeError("ollama is down")
        return self.reply


class Services:
    def __init__(self, llm):
        self.llm = llm
        self.chat_batch = ChatBatch()


class NoitaTest(unittest.TestCase):
    def test_death_with_a_killer(self):
        llm = FakeLLM("  Hiisi Grenadier got you with an explosion!\n Next time, wear a big hat.  ")
        text, event_type = route("dabi.noita.death", GRENADIER, Services(llm))
        self.assertEqual(event_type, "dabi.tts.ready")
        self.assertEqual(text, "Hiisi Grenadier got you with an explosion! Next time, wear a big hat.")
        self.assertEqual(llm.prompts, [
            "Pdgeorge just died in the mines in Noita, the video game they're playing on stream. "
            "It's only a game, so be silly, not sad. Talk to Pdgeorge: "
            'say "Hiisi Grenadier got you with an explosion!", then give them one silly tip for next time.'
        ])

    def test_nothing_to_blame_while_polymorphed(self):
        self.assertEqual(noita.what_happened(SHEEP_IN_ACID), "You died from acid while you were a Flying Sheep")
        self.assertTrue(noita.death_prompt(SHEEP_IN_ACID).startswith("Pdgeorge just died in the Overgrown cavern in Noita"))

    def test_self_kill_never_says_yourself(self):
        # The bridge calls the killer "Yourself", which would sound like Dabi did it.
        self.assertEqual(noita.what_happened(SELF_KILL), "You blew yourself up")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": "projectile"}), "You hit yourself with your own spell")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": "fire"}), "You got yourself with fire")
        self.assertEqual(noita.what_happened({**SELF_KILL, "cause": ""}), "You got yourself somehow")
        for text in [noita.death_prompt(SELF_KILL), noita.death_fallback(SELF_KILL)]:
            self.assertNotIn("Yourself got", text)

    def test_causes_read_as_words(self):
        for cause, said in [
            ("turned to gold", "You died from being turned to gold"),
            ("drowning", "You died from drowning"),
            ("the Dark Sun", "You died from the Dark Sun"),
            ("melee", "You died from a melee hit"),
            ("", ""),
        ]:
            self.assertEqual(noita.what_happened({"cause": cause}), said)

    def test_places(self):
        self.assertTrue(noita.death_prompt({"cause": "fire", "place": "The Vault"}).startswith("Pdgeorge just died in The Vault in Noita,"))
        for odd in ["", "unknown", "East $0"]:
            self.assertTrue(noita.death_prompt({"cause": "fire", "place": odd}).startswith("Pdgeorge just died in Noita,"), odd)

    def test_names_left_in_finnish(self):
        # names.json keeps Hämis and Ukko as they are, and an unknown name comes through as the game wrote it.
        self.assertEqual(noita.what_happened({"killer": "Hämis", "killer_en": "Hämis", "cause": "melee"}), "Hämis got you with a melee hit")
        self.assertEqual(noita.what_happened({"killer": "Uusiolento"}), "Uusiolento got you")
        self.assertEqual(noita.what_happened({"cause": "fire", "polymorphed_to": "Ukko"}), "You died from fire while you were an Ukko")

    def test_when_the_game_didnt_say_how(self):
        self.assertEqual(noita.what_happened({"polymorphed_to_en": "Fish"}), "You died while you were a Fish")
        self.assertTrue(noita.death_prompt({}).endswith("Talk to Pdgeorge: give them one silly tip for next time."))

    def test_pending_chat_is_dropped(self):
        for handle, payload in [(noita.handle_death, GRENADIER), (noita.handle_win, WIN)]:
            services = Services(FakeLLM())
            services.chat_batch.set_enabled(True)
            services.chat_batch.add("Lumi", "hi dabi")
            handle(payload, services)
            self.assertEqual(services.chat_batch.pending, 0)

    def test_still_reacts_when_the_llm_fails(self):
        for llm in [FakeLLM(fail=True), FakeLLM("   ")]:
            self.assertEqual(noita.handle_death(SHEEP_IN_ACID, Services(llm)), "Oh no, Pdgeorge! You died from acid while you were a Flying Sheep!")
        failing = Services(FakeLLM(fail=True))
        self.assertEqual(noita.handle_death(GRENADIER, failing), "Oh no, Pdgeorge! Hiisi Grenadier got you with an explosion!")
        self.assertEqual(noita.handle_death(SELF_KILL, failing), "Oh no, Pdgeorge! You blew yourself up!")
        self.assertEqual(noita.handle_death({}, failing), "Oh no, Pdgeorge, you died!")
        self.assertEqual(noita.handle_win(WIN, failing), "You won Noita, Pdgeorge! I knew you could do it!")

    def test_win(self):
        llm = FakeLLM("Yay, Pdgeorge!")
        self.assertEqual(route("dabi.noita.win", WIN, Services(llm)), ("Yay, Pdgeorge!", "dabi.tts.ready"))
        self.assertIn("just won Noita, the game they're playing on stream, after about 3 hours, with 11 orbs!", llm.prompts[0])
        self.assertIn("Talk to Pdgeorge: cheer for them", llm.prompts[0])
        for payload in [{}, {"orbs": 0, "playtime": "?"}, {"orbs": "lots"}]:
            self.assertIn("just won Noita, the game they're playing on stream! ", noita.win_prompt(payload))
        self.assertIn("with 1 orb!", noita.win_prompt({"orbs": 1}))

    def test_playtime_as_words(self):
        for playtime, said in [
            ("7:03:05", "about 7 hours"), ("1:00:00", "about 1 hour"), ("0:45:10", "45 minutes"),
            ("0:01:59", "1 minute"), ("0:00:30", "less than a minute"), ("", ""), (None, ""), ("1:2", ""),
        ]:
            self.assertEqual(noita.playtime_words(playtime), said, playtime)


if __name__ == "__main__":
    unittest.main()
