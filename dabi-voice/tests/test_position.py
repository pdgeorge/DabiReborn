"""
dabi-voice/tests/test_position.py
---------------------------------
Where Dabi sits: !dabi, the shop's dabi.body, and what a page that just
connected is told. No RabbitMQ or TTS needed: broadcast() is swapped for a
list.

From dabi-voice/, with its requirements installed:
    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("DABI_POS_X", None)
os.environ.pop("DABI_POS_Y", None)

import app  # noqa: E402


def chat(text, chatter="1"):
    return {"event": {"message": {"text": text}, "chatter_user_id": chatter,
                      "broadcaster_user_id": "1", "chatter_user_login": "someone"}}


class PositionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.sent = []

        async def fake_broadcast(message):
            self.sent.append(message)

        self.real_broadcast = app.broadcast
        app.broadcast = fake_broadcast
        app.INITIAL_POSITION = app.home_position = app.avatar_position = None

    async def asyncTearDown(self):
        app.broadcast = self.real_broadcast

    async def test_dabi_command_walks_him_to_his_new_spot(self):
        await app._handle_chat_message(chat("!dabi 1500, 900"))
        self.assertEqual(self.sent, [{"type": "dabi.position", "x": 1500, "y": 900, "seat": 0, "walk": True}])
        self.assertEqual(app.home_position, {"x": 1500, "y": 900})

    async def test_shop_walk_there_and_home_again(self):
        await app._handle_chat_message(chat("!dabi 300 1000"))
        await app._handle_body({"x": 1682, "y": 859, "seat": 210})
        await app._handle_body({"home": True})
        self.assertEqual(self.sent[1:], [
            {"type": "dabi.position", "x": 1682, "y": 859, "seat": 210, "walk": True},
            {"type": "dabi.position", "x": 300, "y": 1000, "seat": 0, "walk": True},
        ])
        self.assertEqual(app.home_position, {"x": 300, "y": 1000})   # the shop never moves his home

    async def test_a_page_that_connects_is_told_where_he_is_without_a_walk(self):
        await app._handle_body({"x": 1682, "y": 859, "seat": 210})
        self.assertEqual(app._position_payload(),
                         {"type": "dabi.position", "x": 1682, "y": 859, "seat": 210, "walk": False})

    async def test_home_with_no_spot_set_is_the_default(self):
        await app._handle_body({"x": 1682, "y": 859, "seat": 210})
        await app._handle_body({"home": True})
        self.assertEqual(self.sent[-1], {"type": "dabi.position", "x": None, "y": None, "seat": 0, "walk": True})

    async def test_reset_goes_back_to_the_env_spot(self):
        app.INITIAL_POSITION = {"x": 100, "y": 1000}
        await app._handle_chat_message(chat("!dabi 1500, 900"))
        await app._handle_chat_message(chat("!dabi reset"))
        self.assertEqual(self.sent[-1], {"type": "dabi.position", "x": 100, "y": 1000, "seat": 0, "walk": True})
        self.assertEqual(app.home_position, {"x": 100, "y": 1000})

    async def test_only_the_broadcaster_moves_him(self):
        await app._handle_chat_message(chat("!dabi 1500, 900", chatter="2"))
        self.assertEqual(self.sent, [])

    async def test_a_walk_without_a_spot_is_ignored(self):
        for bad in ({}, {"x": 5}, {"x": "left", "y": 2}, {"x": None, "y": 3}):
            await app._handle_body(bad)
        self.assertEqual(self.sent, [])
        self.assertIsNone(app.avatar_position)

    async def test_seat_is_never_below_the_floor(self):
        await app._handle_body({"x": 10, "y": 20, "seat": -50})
        self.assertEqual(self.sent[-1]["seat"], 0)


if __name__ == "__main__":
    unittest.main()
