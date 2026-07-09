from engine.observation import Observation
from engine.tiles import tiles_to_34_array
from policy.rule_based import ShantenDiscardPolicy, _shanten_for


def test_discards_the_isolated_honor_over_useful_tiles():
    # Tenpai-ish hand plus one stray honor tile that helps nothing.
    hand = [
        "1m", "2m", "3m",
        "4p", "5p", "6p",
        "7s", "8s", "9s",
        "2s", "2s", "2s",
        "5m", "E",
    ]
    action = ShantenDiscardPolicy().act(Observation(seat=0, hand=hand, drawn_tile="E"))
    assert action.pai == "E"


def test_never_makes_shanten_worse_than_the_best_available_discard():
    hand = ["1m", "1m", "2m", "3m", "5p", "6p", "7p", "9s", "9s", "E", "S", "W", "N", "P"]
    best_possible = min(
        _shanten_for([t for i, t in enumerate(hand) if i != idx])
        for idx in range(len(hand))
    )

    action = ShantenDiscardPolicy().act(Observation(seat=0, hand=hand, drawn_tile="P"))

    remaining = hand.copy()
    remaining.remove(action.pai)
    assert _shanten_for(remaining) == best_possible


def test_tiles_to_34_array_counts_red_five_as_a_five():
    assert tiles_to_34_array(["5m", "5mr"])[4] == 2
