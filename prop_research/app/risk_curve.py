from __future__ import annotations

from dataclasses import replace

from prop_research.domain.config import PropFirmConfig
from prop_research.domain.enums import CycleState, PersonalState, PropState
from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.base import PersonalRiskStrategy


def build_risk_curve(
    config: PropFirmConfig,
    strategy: PersonalRiskStrategy,
    stage_key: str,
    personal_balance: float,
    prop_risk_percent: float,
    points: int = 61,
) -> list[dict[str, float | str]]:
    if points < 2:
        raise ValueError("points must be at least 2")

    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    runtime_config = replace(config, prop_risk_per_trade=prop_risk_amount)
    stage_name, min_pnl, max_pnl, stage_index, prop_state = _stage_bounds(runtime_config, stage_key)

    rows: list[dict[str, float | str]] = []
    step = (max_pnl - min_pnl) / (points - 1)

    for index in range(points):
        prop_pnl = min_pnl + step * index
        snapshot = _snapshot_for_point(
            config=runtime_config,
            prop_state=prop_state,
            stage_index=stage_index,
            prop_pnl=prop_pnl,
            personal_balance=personal_balance,
        )
        decision = strategy.decide(snapshot)
        risk_amount = round(decision.personal_risk_amount, 2)
        rows.append(
            {
                "Стадия": stage_name,
                "PnL пропа, $": round(prop_pnl, 2),
                "PnL пропа, %": round(prop_pnl / runtime_config.nominal_balance * 100, 2),
                "До цели, $": round(snapshot.distance_to_profit_target, 2),
                "До max loss, $": round(snapshot.distance_to_max_loss, 2),
                "Риск пропа, $": round(prop_risk_amount, 2),
                "Риск личного счета, $": risk_amount,
                "Риск личного / риск пропа, %": round(risk_amount / prop_risk_amount * 100, 2)
                if prop_risk_amount > 0
                else 0.0,
            }
        )

    return rows


def _stage_bounds(config: PropFirmConfig, stage_key: str) -> tuple[str, float, float, int, PropState]:
    if stage_key == "funded":
        return (
            "Funded до первой выплаты",
            -config.funded.max_loss,
            config.funded.profit_target_for_first_payout,
            len(config.stages),
            PropState.FUNDED_PRE_PAYOUT,
        )

    if not stage_key.startswith("phase_"):
        raise ValueError("stage_key must be phase_N or funded")

    stage_number = int(stage_key.replace("phase_", ""))
    stage_index = stage_number - 1
    stage = config.stages[stage_index]
    return (
        f"Этап {stage_number}: {stage.name}",
        -stage.max_loss,
        stage.profit_target,
        stage_index,
        PropState.CHALLENGE_PHASE,
    )


def _snapshot_for_point(
    config: PropFirmConfig,
    prop_state: PropState,
    stage_index: int,
    prop_pnl: float,
    personal_balance: float,
) -> StateSnapshot:
    return StateSnapshot(
        config=config,
        prop_state=prop_state,
        personal_state=PersonalState.PERSONAL_ACTIVE,
        cycle_state=CycleState.CYCLE_RUNNING,
        stage_index=stage_index,
        stage_pnl=prop_pnl if prop_state == PropState.CHALLENGE_PHASE else 0.0,
        funded_pnl=prop_pnl if prop_state == PropState.FUNDED_PRE_PAYOUT else 0.0,
        personal_balance=personal_balance,
        challenge_fees_paid=config.challenge_fee,
        external_topups_paid=0.0,
        net_payouts_received=0.0,
        trades_in_stage=0,
        total_trades=0,
    )
