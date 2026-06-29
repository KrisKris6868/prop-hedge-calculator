from __future__ import annotations

from dataclasses import dataclass

from prop_research.domain.enums import PropState
from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.base import PersonalRiskDecision, decision_from_amount


@dataclass(frozen=True)
class ZonedPersonalRiskStrategy:
    near_loss_multiplier: float = 0.08
    mid_multiplier: float = 0.04
    near_target_multiplier: float = 0.01
    funded_multiplier: float = 0.0
    near_loss_threshold: float = 0.25
    near_target_threshold: float = 0.20

    def decide(self, snapshot: StateSnapshot) -> PersonalRiskDecision:
        if snapshot.prop_state == PropState.FUNDED_PRE_PAYOUT:
            return decision_from_amount(snapshot.config.prop_risk_per_trade * self.funded_multiplier)

        total_range = snapshot.active_profit_target + snapshot.active_max_loss
        if total_range <= 0:
            return decision_from_amount(0.0)

        loss_distance_ratio = snapshot.distance_to_max_loss / snapshot.active_max_loss
        target_distance_ratio = snapshot.distance_to_profit_target / snapshot.active_profit_target

        if loss_distance_ratio <= self.near_loss_threshold:
            multiplier = self.near_loss_multiplier
        elif target_distance_ratio <= self.near_target_threshold:
            multiplier = self.near_target_multiplier
        else:
            multiplier = self.mid_multiplier

        return decision_from_amount(snapshot.config.prop_risk_per_trade * multiplier)

