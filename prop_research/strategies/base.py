from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from prop_research.domain.enums import PersonalDirection
from prop_research.domain.snapshot import StateSnapshot


@dataclass(frozen=True)
class PersonalRiskDecision:
    personal_risk_amount: float
    personal_direction: PersonalDirection = PersonalDirection.OPPOSITE_PROP
    allow_personal_trade: bool = True


class PersonalRiskStrategy(Protocol):
    def decide(self, snapshot: StateSnapshot) -> PersonalRiskDecision:
        """Return personal-account risk for the next prop trade."""


def decision_from_amount(amount: float) -> PersonalRiskDecision:
    clean_amount = max(0.0, amount)
    return PersonalRiskDecision(
        personal_risk_amount=clean_amount,
        personal_direction=PersonalDirection.OPPOSITE_PROP if clean_amount > 0 else PersonalDirection.NONE,
        allow_personal_trade=clean_amount > 0,
    )

