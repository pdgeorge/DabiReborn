from mahjong.shanten import Shanten

from engine.action import Action
from engine.observation import Observation
from engine.tiles import HONORS, tiles_to_34_array
from policy.base import Policy

_shanten_calculator = Shanten()


def _shanten_for(hand: list[str]) -> int:
    return _shanten_calculator.calculate_shanten(tiles_to_34_array(hand))


def _tie_break_key(tile: str) -> tuple[int, str]:
    """Among equally-good discards, prefer shedding honors, then terminals,
    then whatever's left — a rough, not-learned proxy for "least useful"."""
    if tile in HONORS:
        return (0, tile)
    rank = int(tile[0])
    if rank in (1, 9):
        return (1, tile)
    return (2, tile)


class ShantenDiscardPolicy(Policy):
    """Phase 1 baseline: always discard whichever tile leaves the hand at
    the lowest shanten (closest to tenpai). No calls, no riichi — just
    proof that the Observation -> Action plumbing works end to end."""

    def act(self, observation: Observation) -> Action:
        hand = list(observation.hand)
        best_tile = None
        best_shanten = None

        for tile in sorted(set(hand), key=_tie_break_key):
            remaining = hand.copy()
            remaining.remove(tile)
            shanten = _shanten_for(remaining)
            if best_shanten is None or shanten < best_shanten:
                best_shanten = shanten
                best_tile = tile

        return Action.discard(best_tile)
