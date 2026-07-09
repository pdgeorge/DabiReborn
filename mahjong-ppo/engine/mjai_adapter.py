"""Bridges mjai JSON-lines messages to our own Observation/Action types.

This is the one file that's allowed to know about mjai wire format — the
policy layer never sees raw mjai JSON. Only the messages needed for the
phase 1 discard-only baseline (start_kyoku, tsumo, dahai) are implemented.

TODO(calls): pon/chi/kan/reach/hora message shapes below are not hand-verified
against a real mjai game log yet. Confirm field names before building the
call-aware policy in phase 3.
"""

import json

from engine.action import Action, ActionType
from engine.observation import Observation
from policy.base import Policy


class MjaiAdapter:
    def __init__(self, seat: int, policy: Policy):
        self.seat = seat
        self.policy = policy
        self.hand: list[str] = []

    def handle_line(self, line: str) -> str | None:
        """Feed one incoming mjai message; return an outgoing mjai message
        (as a JSON string) if this adapter needs to respond, else None."""
        msg = json.loads(line)
        msg_type = msg.get("type")

        if msg_type == "start_kyoku":
            self.hand = list(msg["tehais"][self.seat])
            return None

        if msg_type == "tsumo":
            if msg["actor"] != self.seat:
                return None
            self.hand.append(msg["pai"])
            observation = Observation(
                seat=self.seat,
                hand=list(self.hand),
                drawn_tile=msg["pai"],
            )
            action = self.policy.act(observation)
            return self._encode(action)

        if msg_type == "dahai" and msg["actor"] == self.seat:
            # Our own discard being echoed back by the game — reconcile hand.
            if msg["pai"] in self.hand:
                self.hand.remove(msg["pai"])
            return None

        # TODO(calls): pon/chi/kan/reach/hora/ryukyoku handling goes here
        # once the policy's action space grows past plain discards.
        return None

    def _encode(self, action: Action) -> str:
        if action.type == ActionType.DAHAI:
            return json.dumps({"type": "dahai", "actor": self.seat, "pai": action.pai, "tsumogiri": False})
        raise NotImplementedError(f"encoding for {action.type} not implemented yet")
