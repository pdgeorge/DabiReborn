"""
Tests for Dabi thanking shop buyers (handlers/shop_purchase.py), with a fake
LLM. Run from dabi-stream-brain: python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chat_batch import ChatBatch  # noqa: E402
from handlers import shop_purchase  # noqa: E402
from router import route  # noqa: E402

PURCHASE = {"user_name": "PixelPete", "user_login": "pixelpete", "item": "shirtA", "slot": "torso", "copies": 1, "cost": 300}


class FakeLLM:
    def __init__(self, reply="Thank you!", fail=False):
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


class ShopPurchaseTest(unittest.TestCase):
    def test_item_names_are_said_as_words(self):
        for item_id, said in [("shirtA", "shirt A"), ("bronze_first", "bronze first"), ("mane", "mane"), ("TopHat", "Top Hat"), ("hat-2", "hat 2")]:
            self.assertEqual(shop_purchase.item_name(item_id), said)

    def test_thanks_the_buyer_for_the_item(self):
        llm = FakeLLM("  Thank you PixelPete!\n Shirt A is made of sunshine.  ")
        text, event_type = route("dabi.shop.purchase", PURCHASE, Services(llm))
        self.assertEqual(event_type, "dabi.tts.ready")
        self.assertEqual(text, "Thank you PixelPete! Shirt A is made of sunshine.")
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("Talk to PixelPete", llm.prompts[0])
        self.assertIn('say "shirt A"', llm.prompts[0])
        self.assertIn("how good they'll look in it", llm.prompts[0])

    def test_another_copy(self):
        llm = FakeLLM()
        shop_purchase.handle({**PURCHASE, "item": "tophat", "slot": "head", "copies": 3}, Services(llm))
        self.assertIn('another "tophat"', llm.prompts[0])
        self.assertIn("now they have 3", llm.prompts[0])

    def test_pending_chat_is_dropped(self):
        services = Services(FakeLLM())
        services.chat_batch.set_enabled(True)
        services.chat_batch.add("Lumi", "hi dabi")
        shop_purchase.handle(PURCHASE, services)
        self.assertEqual(services.chat_batch.pending, 0)

    def test_still_thanks_them_when_the_llm_fails(self):
        for llm in [FakeLLM(fail=True), FakeLLM("   ")]:
            text = shop_purchase.handle(PURCHASE, Services(llm))
            self.assertEqual(text, "Thank you PixelPete! You're going to look amazing in shirt A!")
        text = shop_purchase.handle({**PURCHASE, "copies": 2}, Services(FakeLLM(fail=True)))
        self.assertIn("Now you've got 2", text)

    def test_falls_back_to_the_login_and_ignores_bad_purchases(self):
        llm = FakeLLM()
        shop_purchase.handle({**PURCHASE, "user_name": ""}, Services(llm))
        self.assertIn("Talk to pixelpete", llm.prompts[0])
        for bad in [{**PURCHASE, "user_name": "", "user_login": ""}, {**PURCHASE, "item": ""}]:
            self.assertIsNone(shop_purchase.handle(bad, Services(llm)))
        self.assertEqual(len(llm.prompts), 1)
        shop_purchase.handle({**PURCHASE, "copies": "lots"}, Services(llm))
        self.assertNotIn("another", llm.prompts[-1])


if __name__ == "__main__":
    unittest.main()
