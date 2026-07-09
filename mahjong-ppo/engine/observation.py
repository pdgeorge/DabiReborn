from dataclasses import dataclass, field


@dataclass
class Observation:
    """Everything the policy is allowed to see when it's asked to act.

    Phase 1 only ever asks the policy to act right after a self-draw
    (tsumo), so this is deliberately minimal: our own hand plus the tile we
    just drew. Dora indicators, discard piles, other players' calls, riichi
    state etc. all belong here once the policy needs them for calls/hora —
    see the TODO in mjai_adapter.py.
    """

    seat: int
    hand: list[str]
    drawn_tile: str | None = None
    legal_action_types: list[str] = field(default_factory=lambda: ["dahai"])
