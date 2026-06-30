from __future__ import annotations

from dataclasses import dataclass, replace

from prop_research.domain.config import PropFirmConfig, StageConfig
from prop_research.domain.enums import CycleState, PersonalState, PropState


@dataclass(frozen=True)
class StateSnapshot:
    config: PropFirmConfig
    prop_state: PropState
    personal_state: PersonalState
    cycle_state: CycleState
    stage_index: int
    stage_pnl: float
    funded_pnl: float
    personal_balance: float
    challenge_fees_paid: float
    external_topups_paid: float
    net_payouts_received: float
    trades_in_stage: int
    total_trades: int

    @classmethod
    def initial(cls, config: PropFirmConfig, initial_personal_balance: float) -> StateSnapshot:
        is_instant = getattr(config, "account_type", "challenge") == "instant"
        return cls(
            config=config,
            prop_state=PropState.FUNDED_PRE_PAYOUT if is_instant else PropState.CHALLENGE_PHASE,
            personal_state=PersonalState.PERSONAL_ACTIVE,
            cycle_state=CycleState.CYCLE_RUNNING,
            stage_index=0,
            stage_pnl=0.0,
            funded_pnl=0.0,
            personal_balance=initial_personal_balance,
            challenge_fees_paid=config.challenge_fee,
            external_topups_paid=max(0.0, initial_personal_balance),
            net_payouts_received=0.0,
            trades_in_stage=0,
            total_trades=0,
        )

    @property
    def current_stage(self) -> StageConfig | None:
        if self.prop_state != PropState.CHALLENGE_PHASE:
            return None
        return self.config.stages[self.stage_index]

    @property
    def active_profit_target(self) -> float:
        if self.prop_state == PropState.FUNDED_PRE_PAYOUT:
            return self.config.funded.profit_target_for_first_payout
        stage = self.current_stage
        if stage is None:
            return 0.0
        return stage.profit_target

    @property
    def active_max_loss(self) -> float:
        if self.prop_state == PropState.FUNDED_PRE_PAYOUT:
            return self.config.funded.max_loss
        stage = self.current_stage
        if stage is None:
            return 0.0
        return stage.max_loss

    @property
    def active_pnl(self) -> float:
        if self.prop_state == PropState.FUNDED_PRE_PAYOUT:
            return self.funded_pnl
        return self.stage_pnl

    @property
    def distance_to_profit_target(self) -> float:
        return max(0.0, self.active_profit_target - self.active_pnl)

    @property
    def distance_to_max_loss(self) -> float:
        return max(0.0, self.active_pnl + self.active_max_loss)

    @property
    def final_wealth(self) -> float:
        return (
            self.net_payouts_received
            - self.challenge_fees_paid
            - self.external_topups_paid
            + self.personal_balance
        )

    def with_stage_pnl(self, stage_pnl: float) -> StateSnapshot:
        return replace(self, stage_pnl=stage_pnl)
