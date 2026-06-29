from __future__ import annotations

from dataclasses import dataclass

from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.base import PersonalRiskDecision, decision_from_amount


@dataclass(frozen=True)
class FixedPersonalRiskStrategy:
    risk_amount: float

    def decide(self, snapshot: StateSnapshot) -> PersonalRiskDecision:
        return decision_from_amount(self.risk_amount)

