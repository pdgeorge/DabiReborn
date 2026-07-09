from dataclasses import dataclass, field
from enum import Enum


class ActionType(Enum):
    NONE = "none"
    DAHAI = "dahai"
    PON = "pon"
    CHI = "chi"
    KAN = "kan"
    REACH = "reach"
    HORA = "hora"


@dataclass
class Action:
    """What the policy decided to do.

    `pai` is the tile involved (discarded, or won on). `consumed` is the
    tiles from hand used to complete a call (pon/chi/kan) — unused by the
    phase 1 baseline, which only ever produces DAHAI actions.
    """

    type: ActionType
    pai: str | None = None
    consumed: list[str] = field(default_factory=list)

    @staticmethod
    def discard(tile: str) -> "Action":
        return Action(type=ActionType.DAHAI, pai=tile)
