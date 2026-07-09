from abc import ABC, abstractmethod

from engine.action import Action
from engine.observation import Observation


class Policy(ABC):
    """The entire contract a decision-maker has to satisfy.

    Everything upstream (mjai adapter today, a PPO env wrapper tomorrow,
    a Mario adapter someday) only ever calls `act`. Swapping the policy
    implementation should never require touching the adapter.
    """

    @abstractmethod
    def act(self, observation: Observation) -> Action:
        raise NotImplementedError
