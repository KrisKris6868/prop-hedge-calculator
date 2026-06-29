from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from prop_research.domain.config import PropFirmConfig


class CoverageMode(str, Enum):
    GROW_DEPOSIT_BY_FEE = "grow_deposit_by_fee"
    BALANCE_COVERS_NEXT_CHALLENGE = "balance_covers_next_challenge"


@dataclass(frozen=True)
class PointRiskRequirement:
    required_personal_risk: float
    loss_trades_to_failure: float
    target_personal_balance_at_failure: float
    personal_profit_if_failure: float


@dataclass(frozen=True)
class StagePlanRow:
    stage_name: str
    profit_target: float
    max_loss: float
    target_trades_to_pass: float
    loss_trades_to_fail: float
    starting_personal_balance: float
    required_personal_risk: float
    personal_loss_if_stage_passed: float
    personal_balance_after_stage_passed: float
    personal_balance_if_stage_failed: float


@dataclass(frozen=True)
class StagePlan:
    rows: list[StagePlanRow]
    prop_risk_amount: float
    personal_loss_if_all_targets_hit: float
    personal_balance_at_first_payout_path: float
    feasible_with_initial_deposit: bool
    mode: CoverageMode


@dataclass(frozen=True)
class FreePropRequirement:
    minimum_personal_deposit: float
    challenge_fee: float
    total_capital_before_payout: float
    total_success_path_personal_loss: float


def minimum_personal_deposit_for_strict_free_prop(
    config: PropFirmConfig,
    prop_risk_percent: float,
) -> FreePropRequirement:
    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    accumulated_success_path_loss = 0.0

    stage_specs = [
        (stage.profit_target, stage.max_loss)
        for stage in config.stages
    ]
    stage_specs.append((config.funded.profit_target_for_first_payout, config.funded.max_loss))

    for profit_target, max_loss in stage_specs:
        shortfall_to_strict_target = config.challenge_fee + accumulated_success_path_loss
        loss_trades_to_fail = max_loss / prop_risk_amount
        target_trades_to_pass = profit_target / prop_risk_amount
        required_personal_risk = shortfall_to_strict_target / loss_trades_to_fail
        accumulated_success_path_loss += required_personal_risk * target_trades_to_pass

    minimum_deposit = accumulated_success_path_loss
    return FreePropRequirement(
        minimum_personal_deposit=round(minimum_deposit, 2),
        challenge_fee=round(config.challenge_fee, 2),
        total_capital_before_payout=round(minimum_deposit + config.challenge_fee, 2),
        total_success_path_personal_loss=round(accumulated_success_path_loss, 2),
    )


def required_risk_at_point(
    challenge_fee: float,
    initial_personal_balance: float,
    current_personal_balance: float,
    current_prop_pnl: float,
    max_loss: float,
    prop_risk_amount: float,
    mode: CoverageMode,
) -> PointRiskRequirement:
    distance_to_failure = max(0.0, current_prop_pnl + max_loss)
    loss_trades_to_failure = distance_to_failure / prop_risk_amount if prop_risk_amount > 0 else 0.0
    target_balance = _target_balance(
        challenge_fee=challenge_fee,
        initial_personal_balance=initial_personal_balance,
        mode=mode,
    )
    shortfall = max(0.0, target_balance - current_personal_balance)
    required_risk = shortfall / loss_trades_to_failure if loss_trades_to_failure > 0 else 0.0
    return PointRiskRequirement(
        required_personal_risk=round(required_risk, 2),
        loss_trades_to_failure=round(loss_trades_to_failure, 2),
        target_personal_balance_at_failure=round(target_balance, 2),
        personal_profit_if_failure=round(required_risk * loss_trades_to_failure, 2),
    )


def build_stage_plan(
    config: PropFirmConfig,
    initial_personal_balance: float,
    prop_risk_percent: float,
    mode: CoverageMode,
) -> StagePlan:
    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    personal_balance = initial_personal_balance
    rows: list[StagePlanRow] = []

    stage_specs = [
        (f"Этап {index + 1}: {stage.name}", stage.profit_target, stage.max_loss)
        for index, stage in enumerate(config.stages)
    ]
    stage_specs.append(
        (
            "Funded до первой выплаты",
            config.funded.profit_target_for_first_payout,
            config.funded.max_loss,
        )
    )

    for stage_name, profit_target, max_loss in stage_specs:
        requirement = required_risk_at_point(
            challenge_fee=config.challenge_fee,
            initial_personal_balance=initial_personal_balance,
            current_personal_balance=personal_balance,
            current_prop_pnl=0.0,
            max_loss=max_loss,
            prop_risk_amount=prop_risk_amount,
            mode=mode,
        )
        target_trades_to_pass = profit_target / prop_risk_amount
        loss_trades_to_fail = max_loss / prop_risk_amount
        personal_loss_if_passed = requirement.required_personal_risk * target_trades_to_pass
        balance_after_pass = personal_balance - personal_loss_if_passed
        balance_if_failed = personal_balance + requirement.required_personal_risk * loss_trades_to_fail

        rows.append(
            StagePlanRow(
                stage_name=stage_name,
                profit_target=round(profit_target, 2),
                max_loss=round(max_loss, 2),
                target_trades_to_pass=round(target_trades_to_pass, 2),
                loss_trades_to_fail=round(loss_trades_to_fail, 2),
                starting_personal_balance=round(personal_balance, 2),
                required_personal_risk=requirement.required_personal_risk,
                personal_loss_if_stage_passed=round(personal_loss_if_passed, 2),
                personal_balance_after_stage_passed=round(balance_after_pass, 2),
                personal_balance_if_stage_failed=round(balance_if_failed, 2),
            )
        )
        personal_balance = balance_after_pass

    total_loss = initial_personal_balance - personal_balance
    return StagePlan(
        rows=rows,
        prop_risk_amount=round(prop_risk_amount, 2),
        personal_loss_if_all_targets_hit=round(total_loss, 2),
        personal_balance_at_first_payout_path=round(personal_balance, 2),
        feasible_with_initial_deposit=personal_balance >= 0,
        mode=mode,
    )


def build_dealing_instruction(
    config: PropFirmConfig,
    initial_personal_balance: float,
    prop_risk_percent: float,
    mode: CoverageMode,
) -> list[dict[str, float | str]]:
    plan = build_stage_plan(
        config=config,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=mode,
    )
    instruction: list[dict[str, float | str]] = []

    cumulative_personal_cost = 0.0
    for row in plan.rows:
        cumulative_personal_cost += row.personal_loss_if_stage_passed
        payout_columns: dict[str, float] = {}
        if row.stage_name == "Funded до первой выплаты":
            gross_profit = config.funded.profit_target_for_first_payout
            payout_after_split = gross_profit * config.funded.trader_split
            payout_columns = {
                "Gross profit до выплаты, $": round(gross_profit, 2),
                "Профит сплит, %": round(config.funded.trader_split * 100, 2),
                "К выплате после сплита, $": round(payout_after_split, 2),
                "Затраты личного счета до выплаты, $": round(cumulative_personal_cost, 2),
                "Чистыми после личных затрат, $": round(payout_after_split - cumulative_personal_cost, 2),
            }
        instruction.append(
            {
                "Стадия": row.stage_name,
                "Риск пропа, %": round(prop_risk_percent, 2),
                "Риск пропа, $": plan.prop_risk_amount,
                "Риск личного, $": row.required_personal_risk,
                "Риск личного, % от проп-риска": round(
                    row.required_personal_risk / plan.prop_risk_amount * 100,
                    2,
                )
                if plan.prop_risk_amount > 0
                else 0.0,
                "Цель стадии, $": row.profit_target,
                "Max loss стадии, $": row.max_loss,
                "Win до прохода стадии": row.target_trades_to_pass,
                "Loss до потери пропа": row.loss_trades_to_fail,
                "Старт личного счета, $": row.starting_personal_balance,
                "Личная просадка при проходе стадии, $": row.personal_loss_if_stage_passed,
                "Личный счет после прохода стадии, $": row.personal_balance_after_stage_passed,
                "Личный счет при потере пропа, $": row.personal_balance_if_stage_failed,
                **payout_columns,
            }
        )

    return instruction


def calculate_personal_balance_from_prop_pnl(
    config: PropFirmConfig,
    stage_key: str,
    current_prop_pnl: float,
    initial_personal_balance: float,
    prop_risk_percent: float,
    mode: CoverageMode,
) -> dict[str, float | str]:
    plan = build_stage_plan(
        config=config,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=mode,
    )
    row = _stage_plan_row_for_key(plan, stage_key)
    prop_risk_amount = plan.prop_risk_amount
    pnl_in_prop_r_units = current_prop_pnl / prop_risk_amount if prop_risk_amount > 0 else 0.0
    personal_change = -pnl_in_prop_r_units * row.required_personal_risk
    current_personal_balance = row.starting_personal_balance + personal_change
    return {
        "Стадия": row.stage_name,
        "Старт личного счета на стадии, $": round(row.starting_personal_balance, 2),
        "Текущий PnL пропа, $": round(current_prop_pnl, 2),
        "Изменение личного счета на стадии, $": round(personal_change, 2),
        "Текущий баланс личного счета, $": round(current_personal_balance, 2),
    }


def calculate_personal_risk_for_trade(
    config: PropFirmConfig,
    stage_key: str,
    current_prop_pnl: float,
    initial_personal_balance: float,
    current_personal_balance: float,
    prop_risk_percent: float,
    mode: CoverageMode,
) -> dict[str, float | str]:
    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    stage_name, max_loss = _calculator_stage(stage_key, config)
    requirement = required_risk_at_point(
        challenge_fee=config.challenge_fee,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        current_prop_pnl=current_prop_pnl,
        max_loss=max_loss,
        prop_risk_amount=prop_risk_amount,
        mode=mode,
    )
    return {
        "Стадия": stage_name,
        "Текущий PnL пропа, $": round(current_prop_pnl, 2),
        "Риск пропа, %": round(prop_risk_percent, 2),
        "Риск пропа, $": round(prop_risk_amount, 2),
        "Риск личного, $": requirement.required_personal_risk,
        "Loss до потери пропа": requirement.loss_trades_to_failure,
        "Цель личного счета при потере пропа, $": requirement.target_personal_balance_at_failure,
        "Ожидаемый личный счет при потере пропа, $": round(
            current_personal_balance + requirement.personal_profit_if_failure,
            2,
        ),
        "Если на пропе Long": "на личном Short",
        "Если на пропе Short": "на личном Long",
    }


def _stage_plan_row_for_key(plan: StagePlan, stage_key: str) -> StagePlanRow:
    if stage_key == "funded":
        return plan.rows[-1]
    stage_number = int(stage_key.replace("phase_", ""))
    return plan.rows[stage_number - 1]


def _calculator_stage(stage_key: str, config: PropFirmConfig) -> tuple[str, float]:
    if stage_key == "funded":
        return "Funded до первой выплаты", config.funded.max_loss
    stage_number = int(stage_key.replace("phase_", ""))
    stage = config.stages[stage_number - 1]
    return f"Этап {stage_number}: {stage.name}", stage.max_loss


def _target_balance(challenge_fee: float, initial_personal_balance: float, mode: CoverageMode) -> float:
    if mode == CoverageMode.GROW_DEPOSIT_BY_FEE:
        return initial_personal_balance + challenge_fee
    return challenge_fee
