from __future__ import annotations

from dataclasses import dataclass

from prop_research.domain.enums import PropState
from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.base import PersonalRiskDecision, decision_from_amount


@dataclass(frozen=True)
class ContinuousPersonalRiskStrategy:
    min_multiplier: float = 0.01
    max_multiplier: float = 0.08
    funded_multiplier: float = 0.0

    def decide(self, snapshot: StateSnapshot) -> PersonalRiskDecision:
        if snapshot.prop_state == PropState.FUNDED_PRE_PAYOUT:
            return decision_from_amount(snapshot.config.prop_risk_per_trade * self.funded_multiplier)
        if snapshot.active_max_loss <= 0 or snapshot.active_profit_target <= 0:
            return decision_from_amount(0.0)

        loss_pressure = 1.0 - min(1.0, snapshot.distance_to_max_loss / snapshot.active_max_loss)
        target_pressure = 1.0 - min(1.0, snapshot.distance_to_profit_target / snapshot.active_profit_target)
        balance_shortfall = max(0.0, snapshot.config.challenge_fee - snapshot.personal_balance)
        shortfall_pressure = min(1.0, balance_shortfall / snapshot.config.challenge_fee)

        pressure = 0.5 + (0.5 * loss_pressure) - (0.5 * target_pressure) + (0.25 * shortfall_pressure)
        pressure = max(0.0, min(1.0, pressure))
        multiplier = self.min_multiplier + (self.max_multiplier - self.min_multiplier) * pressure
        return decision_from_amount(snapshot.config.prop_risk_per_trade * multiplier)
