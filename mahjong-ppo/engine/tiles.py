"""Tile notation shared by the engine and policy layers.

Tiles are plain strings in mjai-ish notation: "1m".."9m", "1p".."9p",
"9s".."9s" for the three suits, and "E","S","W","N","P","F","C" for the
honors (winds + haku/hatsu/chun). A trailing "r" marks a red five
("5mr") — for shanten purposes a red five is just a five, the distinction
only matters for scoring, which is out of scope for the phase 1 baseline.

The 34-index ordering (man 1-9, pin 1-9, sou 1-9, then the 7 honors) matches
what `mahjong.shanten.Shanten` expects.
"""

HONORS = ["E", "S", "W", "N", "P", "F", "C"]
SUITS = ["m", "p", "s"]


def tile_to_34_index(tile: str) -> int:
    if tile in HONORS:
        return 27 + HONORS.index(tile)

    rank_str, suit = tile[:-1], tile[-1]
    if suit == "r":
        # red five, e.g. "5mr" -> rank "5", suit "m"
        rank_str, suit = tile[:-2], tile[-2]

    rank = int(rank_str)
    if suit not in SUITS:
        raise ValueError(f"unrecognised tile: {tile!r}")

    return SUITS.index(suit) * 9 + (rank - 1)


def tiles_to_34_array(tiles: list[str]) -> list[int]:
    counts = [0] * 34
    for tile in tiles:
        counts[tile_to_34_index(tile)] += 1
    return counts
