from __future__ import annotations

from dataclasses import replace

from prop_research.domain.config import PropFirmConfig
from prop_research.domain.enums import CycleState, PersonalState, PropState
from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.base import PersonalRiskStrategy


class PropStateMachine:
    def __init__(self, snapshot: StateSnapshot, strategy: PersonalRiskStrategy) -> None:
        self.snapshot = snapshot
        self.strategy = strategy

    @classmethod
    def start(
        cls,
        config: PropFirmConfig,
        initial_personal_balance: float,
        strategy: PersonalRiskStrategy,
    ) -> PropStateMachine:
        return cls(
            snapshot=StateSnapshot.initial(
                config=config,
                initial_personal_balance=initial_personal_balance,
            ),
            strategy=strategy,
        )

    def apply_trade(self, prop_win: bool) -> StateSnapshot:
        if self.snapshot.cycle_state != CycleState.CYCLE_RUNNING:
            return self.snapshot

        decision = self.strategy.decide(self.snapshot)
        prop_pnl = self.snapshot.config.prop_risk_per_trade if prop_win else -self.snapshot.config.prop_risk_per_trade
        personal_pnl = 0.0
        if decision.allow_personal_trade:
            personal_pnl = -decision.personal_risk_amount if prop_win else decision.personal_risk_amount

        updated_personal_balance = self.snapshot.personal_balance + personal_pnl

        if self.snapshot.prop_state == PropState.CHALLENGE_PHASE:
            self._apply_challenge_trade(prop_pnl, updated_personal_balance)
        elif self.snapshot.prop_state == PropState.FUNDED_PRE_PAYOUT:
            self._apply_funded_trade(prop_pnl, updated_personal_balance)

        return self.snapshot

    def _apply_challenge_trade(self, prop_pnl: float, personal_balance: float) -> None:
        pnl = self.snapshot.stage_pnl + prop_pnl
        stage = self.snapshot.config.stages[self.snapshot.stage_index]
        high_watermark = max(self.snapshot.prop_high_watermark_pnl, pnl)
        base = replace(
            self.snapshot,
            stage_pnl=pnl,
            prop_high_watermark_pnl=high_watermark,
            personal_balance=personal_balance,
            trades_in_stage=self.snapshot.trades_in_stage + 1,
            total_trades=self.snapshot.total_trades + 1,
        )

        if base.distance_to_max_loss <= 0:
            self.snapshot = self._settle_failure(base)
            return

        if pnl >= stage.profit_target:
            next_index = self.snapshot.stage_index + 1
            if next_index >= len(self.snapshot.config.stages):
                self.snapshot = replace(
                    base,
                    prop_state=PropState.FUNDED_PRE_PAYOUT,
                    stage_index=next_index,
                    stage_pnl=0.0,
                    prop_high_watermark_pnl=0.0,
                    trades_in_stage=0,
                )
            else:
                self.snapshot = replace(base, stage_index=next_index, stage_pnl=0.0, prop_high_watermark_pnl=0.0, trades_in_stage=0)
            return

        self.snapshot = self._refresh_personal_state(base)

    def _apply_funded_trade(self, prop_pnl: float, personal_balance: float) -> None:
        pnl = self.snapshot.funded_pnl + prop_pnl
        high_watermark = max(self.snapshot.prop_high_watermark_pnl, pnl)
        base = replace(
            self.snapshot,
            funded_pnl=pnl,
            prop_high_watermark_pnl=high_watermark,
            personal_balance=personal_balance,
            trades_in_stage=self.snapshot.trades_in_stage + 1,
            total_trades=self.snapshot.total_trades + 1,
        )

        if base.distance_to_max_loss <= 0:
            self.snapshot = self._settle_failure(base)
            return

        if pnl >= self.snapshot.config.funded.profit_target_for_first_payout:
            payout = pnl * self.snapshot.config.funded.trader_split
            self.snapshot = replace(
                base,
                prop_state=PropState.FIRST_PAYOUT_RECEIVED,
                personal_state=PersonalState.PERSONAL_IGNORED_AFTER_PAYOUT,
                cycle_state=CycleState.CYCLE_SUCCESS,
                net_payouts_received=self.snapshot.net_payouts_received + payout,
            )
            return

        self.snapshot = self._refresh_personal_state(base)

    def _settle_failure(self, snapshot: StateSnapshot) -> StateSnapshot:
        personal_state = (
            PersonalState.PERSONAL_COVERS_NEXT_CHALLENGE
            if snapshot.personal_balance >= snapshot.config.challenge_fee
            else PersonalState.PERSONAL_DOES_NOT_COVER_NEXT_CHALLENGE
        )
        cycle_state = (
            CycleState.CYCLE_RECOVERABLE_FAILURE
            if snapshot.personal_balance >= snapshot.config.challenge_fee
            else CycleState.CYCLE_UNRECOVERABLE_FAILURE
        )
        return replace(
            snapshot,
            prop_state=PropState.PROP_FAILED_BEFORE_PAYOUT,
            personal_state=personal_state,
            cycle_state=cycle_state,
        )

    def _refresh_personal_state(self, snapshot: StateSnapshot) -> StateSnapshot:
        if snapshot.personal_balance >= snapshot.config.challenge_fee:
            personal_state = PersonalState.PERSONAL_COVERS_NEXT_CHALLENGE
        elif snapshot.personal_balance <= 0:
            personal_state = PersonalState.PERSONAL_INSUFFICIENT_FOR_HEDGE
        else:
            personal_state = PersonalState.PERSONAL_ACTIVE
        return replace(snapshot, personal_state=personal_state)
