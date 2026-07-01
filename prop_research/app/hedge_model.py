from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from prop_research.domain.config import PropFirmConfig


class CoverageMode(str, Enum):
    GROW_DEPOSIT_BY_FEE = "grow_deposit_by_fee"
    BALANCE_COVERS_NEXT_CHALLENGE = "balance_covers_next_challenge"


class TrailingRiskMode(str, Enum):
    ADAPTIVE = "adaptive"
    CONSERVATIVE = "conservative"
    TARGET_LOCK = "target_lock"
    CURRENT_HIGH_WATERMARK = "current_high_watermark"


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


def calculate_effective_prop_risk(
    max_risk_per_trade: float,
    distance_to_target: float | None,
    distance_to_max_loss: float,
    daily_loss_limit: float | None = None,
) -> float:
    limits = [max(0.0, max_risk_per_trade), max(0.0, distance_to_max_loss)]
    if distance_to_target is not None:
        limits.append(max(0.0, distance_to_target))
    if daily_loss_limit is not None:
        limits.append(max(0.0, daily_loss_limit))
    return round(min(limits), 2)


def minimum_personal_deposit_for_strict_free_prop(
    config: PropFirmConfig,
    prop_risk_percent: float,
    hedge_funded: bool = True,
    trailing_risk_mode: TrailingRiskMode | str = TrailingRiskMode.CONSERVATIVE,
) -> FreePropRequirement:
    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    accumulated_success_path_loss = 0.0
    trailing_risk_mode = _normalize_trailing_risk_mode(trailing_risk_mode)

    stage_specs = [
        (stage.profit_target, stage.max_loss, getattr(stage, "drawdown_mode", "static"))
        for stage in config.stages
    ]
    if hedge_funded:
        stage_specs.append(
            (
                config.funded.profit_target_for_first_payout,
                config.funded.max_loss,
                getattr(config.funded, "drawdown_mode", "static"),
            )
        )

    for profit_target, max_loss, drawdown_mode in stage_specs:
        shortfall_to_strict_target = config.challenge_fee + accumulated_success_path_loss
        loss_trades_to_fail = max_loss / prop_risk_amount
        target_trades_to_pass = profit_target / prop_risk_amount
        recovery_trades_to_fail = _recovery_trades_to_failure(
            target_trades_to_pass=target_trades_to_pass,
            loss_trades_to_fail=loss_trades_to_fail,
            drawdown_mode=drawdown_mode,
            trailing_risk_mode=trailing_risk_mode,
        )
        required_personal_risk = _required_personal_risk(shortfall_to_strict_target, recovery_trades_to_fail)
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
    return _required_risk_for_loss_trades(
        challenge_fee=challenge_fee,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        loss_trades_to_failure=loss_trades_to_failure,
        mode=mode,
    )


def _required_risk_for_loss_trades(
    challenge_fee: float,
    initial_personal_balance: float,
    current_personal_balance: float,
    loss_trades_to_failure: float,
    mode: CoverageMode,
) -> PointRiskRequirement:
    target_balance = _target_balance(
        challenge_fee=challenge_fee,
        initial_personal_balance=initial_personal_balance,
        mode=mode,
    )
    shortfall = max(0.0, target_balance - current_personal_balance)
    required_risk = _required_personal_risk(shortfall, loss_trades_to_failure)
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
    trailing_risk_mode: TrailingRiskMode | str = TrailingRiskMode.CONSERVATIVE,
) -> StagePlan:
    prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    personal_balance = initial_personal_balance
    rows: list[StagePlanRow] = []
    trailing_risk_mode = _normalize_trailing_risk_mode(trailing_risk_mode)

    account_type = getattr(config, "account_type", "challenge")
    stage_specs = [] if account_type == "instant" else [
        (
            f"Этап {index + 1}: {stage.name}",
            stage.profit_target,
            stage.max_loss,
            getattr(stage, "drawdown_mode", "static"),
        )
        for index, stage in enumerate(config.stages)
    ]
    stage_specs.append(
        (
            "Instant счет" if account_type == "instant" else "Funded до первой выплаты",
            config.funded.profit_target_for_first_payout,
            config.funded.max_loss,
            getattr(config.funded, "drawdown_mode", "static"),
        )
    )

    for stage_name, profit_target, max_loss, drawdown_mode in stage_specs:
        target_trades_to_pass = profit_target / prop_risk_amount
        loss_trades_to_fail = max_loss / prop_risk_amount
        recovery_trades_to_fail = _recovery_trades_to_failure(
            target_trades_to_pass=target_trades_to_pass,
            loss_trades_to_fail=loss_trades_to_fail,
            drawdown_mode=drawdown_mode,
            trailing_risk_mode=trailing_risk_mode,
        )
        target_balance = _target_balance(
            challenge_fee=config.challenge_fee,
            initial_personal_balance=initial_personal_balance,
            mode=mode,
        )
        shortfall = max(0.0, target_balance - personal_balance)
        required_personal_risk = round(_required_personal_risk(shortfall, recovery_trades_to_fail), 2)
        personal_loss_if_passed = required_personal_risk * target_trades_to_pass
        balance_after_pass = personal_balance - personal_loss_if_passed
        if drawdown_mode == "trailing" and trailing_risk_mode == TrailingRiskMode.CONSERVATIVE:
            balance_if_failed = balance_after_pass + required_personal_risk * loss_trades_to_fail
        else:
            balance_if_failed = personal_balance + required_personal_risk * loss_trades_to_fail

        rows.append(
            StagePlanRow(
                stage_name=stage_name,
                profit_target=round(profit_target, 2),
                max_loss=round(max_loss, 2),
                target_trades_to_pass=round(target_trades_to_pass, 2),
                loss_trades_to_fail=round(loss_trades_to_fail, 2),
                starting_personal_balance=round(personal_balance, 2),
                required_personal_risk=required_personal_risk,
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
        if row.stage_name in {"Funded до первой выплаты", "Instant счет"}:
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
    trailing_risk_mode: TrailingRiskMode | str = TrailingRiskMode.CONSERVATIVE,
    include_current_prop_pnl: bool = True,
    hedge_wins: float = 0.0,
    hedge_losses: float = 0.0,
    trailing_high_watermark: float | None = None,
) -> dict[str, float | str]:
    plan = build_stage_plan(
        config=config,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=mode,
        trailing_risk_mode=trailing_risk_mode,
    )
    row = _stage_plan_row_for_key(plan, stage_key)
    prop_risk_amount = plan.prop_risk_amount
    drawdown_mode = _stage_drawdown_mode(config, stage_key)
    high_watermark = max(0.0, trailing_high_watermark or current_prop_pnl)
    if (
        include_current_prop_pnl
        and prop_risk_amount > 0
        and drawdown_mode == "trailing"
        and high_watermark > 0
        and current_prop_pnl < high_watermark
    ):
        automatic_hedge_loss = high_watermark / prop_risk_amount * row.required_personal_risk
        balance_at_high_watermark = row.starting_personal_balance - automatic_hedge_loss
        _stage_name, max_loss, _profit_target = _calculator_stage(stage_key, config)
        failure_pnl = high_watermark - max_loss
        effective_current_pnl = max(current_prop_pnl, failure_pnl)
        high_watermark_trade = calculate_personal_risk_for_trade(
            config=config,
            stage_key=stage_key,
            current_prop_pnl=high_watermark,
            initial_personal_balance=initial_personal_balance,
            current_personal_balance=balance_at_high_watermark,
            prop_risk_percent=prop_risk_percent,
            mode=mode,
            max_risk_per_trade=prop_risk_amount,
            trailing_high_watermark=high_watermark,
            trailing_risk_mode=trailing_risk_mode,
        )
        descent_units = max(0.0, high_watermark - effective_current_pnl) / prop_risk_amount
        automatic_hedge_win = descent_units * float(high_watermark_trade["Риск личного, $"])
    else:
        pnl_in_prop_r_units = current_prop_pnl / prop_risk_amount if prop_risk_amount > 0 and include_current_prop_pnl else 0.0
        automatic_hedge_win = max(0.0, -pnl_in_prop_r_units * row.required_personal_risk)
        automatic_hedge_loss = max(0.0, pnl_in_prop_r_units * row.required_personal_risk)
    total_hedge_wins = automatic_hedge_win + max(0.0, hedge_wins)
    total_hedge_losses = automatic_hedge_loss + max(0.0, hedge_losses)
    personal_change = total_hedge_wins - total_hedge_losses
    current_personal_balance = row.starting_personal_balance + personal_change
    return {
        "Стадия": row.stage_name,
        "Старт личного счета на стадии, $": round(row.starting_personal_balance, 2),
        "Текущий PnL пропа, $": round(current_prop_pnl, 2),
        "Изменение личного счета на стадии, $": round(personal_change, 2),
        "Фактический hedge-win, $": round(total_hedge_wins, 2),
        "Фактический hedge-loss, $": round(total_hedge_losses, 2),
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
    max_risk_per_trade: float | None = None,
    target_enabled: bool = True,
    daily_loss_limit: float | None = None,
    hedge_funded: bool = True,
    trailing_high_watermark: float | None = None,
    trailing_risk_mode: TrailingRiskMode | str = TrailingRiskMode.CONSERVATIVE,
) -> dict[str, float | str]:
    full_prop_risk_amount = config.nominal_balance * prop_risk_percent / 100
    stage_name, max_loss, profit_target = _calculator_stage(stage_key, config)
    configured_max_risk = _configured_max_risk_per_trade(config, stage_key)
    requested_max_risk = max_risk_per_trade if max_risk_per_trade is not None else configured_max_risk
    max_trade_risk = min(
        limit
        for limit in [full_prop_risk_amount, requested_max_risk]
        if limit is not None
    )
    distance_to_target = max(0.0, profit_target - current_prop_pnl) if target_enabled else None
    drawdown_mode = _stage_drawdown_mode(config, stage_key)
    trailing_risk_mode = _normalize_trailing_risk_mode(trailing_risk_mode)
    high_watermark = max(current_prop_pnl, trailing_high_watermark or 0.0)
    distance_to_max_loss = _distance_to_max_loss(
        current_prop_pnl=current_prop_pnl,
        max_loss=max_loss,
        drawdown_mode=drawdown_mode,
        trailing_high_watermark=high_watermark,
    )
    effective_prop_risk_amount = calculate_effective_prop_risk(
        max_risk_per_trade=max_trade_risk,
        distance_to_target=distance_to_target,
        distance_to_max_loss=distance_to_max_loss,
        daily_loss_limit=daily_loss_limit,
    )
    recovery_distance_to_failure = _recovery_distance_to_failure_from_point(
        current_prop_pnl=current_prop_pnl,
        profit_target=profit_target,
        max_loss=max_loss,
        drawdown_mode=drawdown_mode,
        trailing_high_watermark=high_watermark,
        target_enabled=target_enabled,
        trailing_risk_mode=trailing_risk_mode,
    )
    actual_loss_trades_to_failure = (
        distance_to_max_loss / effective_prop_risk_amount
        if effective_prop_risk_amount > 0
        else 0.0
    )
    requirement = _required_risk_for_loss_trades(
        challenge_fee=config.challenge_fee,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        loss_trades_to_failure=(
            recovery_distance_to_failure / effective_prop_risk_amount
            if effective_prop_risk_amount > 0
            else 0.0
        ),
        mode=mode,
    )
    current_line_requirement = _required_risk_for_loss_trades(
        challenge_fee=config.challenge_fee,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        loss_trades_to_failure=actual_loss_trades_to_failure,
        mode=mode,
    )
    required_personal_risk = min(requirement.required_personal_risk, current_line_requirement.required_personal_risk)
    personal_risk = 0.0 if stage_key == "funded" and not hedge_funded else round(required_personal_risk, 2)
    personal_risk_percent_of_prop = (
        round(personal_risk / effective_prop_risk_amount * 100, 2)
        if effective_prop_risk_amount > 0
        else 0.0
    )
    prop_to_personal_risk_multiple = (
        round(effective_prop_risk_amount / personal_risk, 2)
        if personal_risk > 0
        else 0.0
    )
    target_status = (
        "Цель отключена"
        if not target_enabled
        else "Цель достигнута"
        if distance_to_target == 0
        else f"До цели осталось ${distance_to_target:,.2f}"
    )
    return {
        "Стадия": stage_name,
        "Текущий PnL пропа, $": round(current_prop_pnl, 2),
        "Риск пропа, %": round(prop_risk_percent, 2),
        "Риск пропа, $": round(effective_prop_risk_amount, 2),
        "Риск личного, $": personal_risk,
        "Loss до потери пропа": round(actual_loss_trades_to_failure, 2),
        "Цель личного счета при потере пропа, $": requirement.target_personal_balance_at_failure,
        "Ожидаемый личный счет при потере пропа, $": round(
            current_personal_balance + personal_risk * actual_loss_trades_to_failure,
            2,
        ),
        "full_prop_risk_amount": round(full_prop_risk_amount, 2),
        "effective_prop_risk_amount": round(effective_prop_risk_amount, 2),
        "distance_to_target": round(distance_to_target, 2) if distance_to_target is not None else 0.0,
        "distance_to_max_loss": round(distance_to_max_loss, 2),
        "personal_risk_percent_of_prop": personal_risk_percent_of_prop,
        "prop_to_personal_risk_multiple": prop_to_personal_risk_multiple,
        "target_status": target_status,
        "Если на пропе Long": "на личном Short",
        "Если на пропе Short": "на личном Long",
    }


def calculate_funded_payout_preview(
    config: PropFirmConfig,
    initial_personal_balance: float,
    prop_risk_percent: float,
    funded_profit: float,
    mode: CoverageMode,
    hedge_funded: bool = True,
    trailing_risk_mode: TrailingRiskMode | str = TrailingRiskMode.CONSERVATIVE,
) -> dict[str, float]:
    plan = build_stage_plan(
        config=config,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=mode,
        trailing_risk_mode=trailing_risk_mode,
    )
    funded_row = plan.rows[-1]
    prop_risk_amount = plan.prop_risk_amount
    funded_win_units = max(0.0, funded_profit) / prop_risk_amount if prop_risk_amount > 0 else 0.0
    challenge_stage_costs = sum(row.personal_loss_if_stage_passed for row in plan.rows[:-1])
    funded_cost = funded_win_units * funded_row.required_personal_risk if hedge_funded else 0.0
    personal_costs_to_current_profit = challenge_stage_costs + funded_cost
    payout_after_split = max(0.0, funded_profit) * config.funded.trader_split
    return {
        "Профит на funded, $": round(max(0.0, funded_profit), 2),
        "Профит сплит, %": round(config.funded.trader_split * 100, 2),
        "К выплате после сплита, $": round(payout_after_split, 2),
        "Затраты личного счета до текущего funded profit, $": round(personal_costs_to_current_profit, 2),
        "Чистыми после личных затрат, $": round(payout_after_split - personal_costs_to_current_profit, 2),
    }


def _stage_plan_row_for_key(plan: StagePlan, stage_key: str) -> StagePlanRow:
    if stage_key == "funded":
        return plan.rows[-1]
    stage_number = int(stage_key.replace("phase_", ""))
    return plan.rows[stage_number - 1]


def _calculator_stage(stage_key: str, config: PropFirmConfig) -> tuple[str, float, float]:
    if stage_key == "funded":
        stage_name = "Instant счет" if getattr(config, "account_type", "challenge") == "instant" else "Funded до первой выплаты"
        return stage_name, config.funded.max_loss, config.funded.profit_target_for_first_payout
    stage_number = int(stage_key.replace("phase_", ""))
    stage = config.stages[stage_number - 1]
    return f"Этап {stage_number}: {stage.name}", stage.max_loss, stage.profit_target


def _configured_max_risk_per_trade(config: PropFirmConfig, stage_key: str) -> float | None:
    if stage_key == "funded":
        return getattr(config.funded, "max_risk_per_trade", None)
    stage_number = int(stage_key.replace("phase_", ""))
    return getattr(config.stages[stage_number - 1], "max_risk_per_trade", None)


def _stage_drawdown_mode(config: PropFirmConfig, stage_key: str) -> str:
    if stage_key == "funded":
        return getattr(config.funded, "drawdown_mode", "static")
    stage_number = int(stage_key.replace("phase_", ""))
    return getattr(config.stages[stage_number - 1], "drawdown_mode", "static")


def _distance_to_max_loss(
    current_prop_pnl: float,
    max_loss: float,
    drawdown_mode: str,
    trailing_high_watermark: float,
) -> float:
    if drawdown_mode == "trailing":
        failure_pnl = trailing_high_watermark - max_loss
        return max(0.0, current_prop_pnl - failure_pnl)
    return max(0.0, current_prop_pnl + max_loss)


def _recovery_distance_to_failure_from_point(
    current_prop_pnl: float,
    profit_target: float,
    max_loss: float,
    drawdown_mode: str,
    trailing_high_watermark: float,
    target_enabled: bool,
    trailing_risk_mode: TrailingRiskMode,
) -> float:
    if drawdown_mode != "trailing":
        return max(0.0, current_prop_pnl + max_loss)
    if trailing_risk_mode == TrailingRiskMode.TARGET_LOCK and target_enabled and current_prop_pnl >= profit_target:
        return 0.0
    future_high_watermark = max(current_prop_pnl, trailing_high_watermark)
    if trailing_risk_mode == TrailingRiskMode.CONSERVATIVE and target_enabled:
        future_high_watermark = max(future_high_watermark, profit_target)
    return max(0.0, max_loss - max(0.0, future_high_watermark - current_prop_pnl))


def _recovery_trades_to_failure(
    target_trades_to_pass: float,
    loss_trades_to_fail: float,
    drawdown_mode: str,
    trailing_risk_mode: TrailingRiskMode,
) -> float:
    if drawdown_mode == "trailing" and trailing_risk_mode == TrailingRiskMode.CONSERVATIVE:
        return max(0.0, loss_trades_to_fail - target_trades_to_pass)
    return max(0.0, loss_trades_to_fail)


def _required_personal_risk(shortfall: float, recovery_trades_to_failure: float) -> float:
    if shortfall <= 0:
        return 0.0
    if recovery_trades_to_failure <= 0:
        return float("inf")
    return shortfall / recovery_trades_to_failure


def _normalize_trailing_risk_mode(trailing_risk_mode: TrailingRiskMode | str) -> TrailingRiskMode:
    if isinstance(trailing_risk_mode, TrailingRiskMode):
        if trailing_risk_mode == TrailingRiskMode.CURRENT_HIGH_WATERMARK:
            return TrailingRiskMode.ADAPTIVE
        return trailing_risk_mode
    try:
        mode = TrailingRiskMode(str(trailing_risk_mode))
        if mode == TrailingRiskMode.CURRENT_HIGH_WATERMARK:
            return TrailingRiskMode.ADAPTIVE
        return mode
    except ValueError:
        return TrailingRiskMode.CONSERVATIVE


def _target_balance(challenge_fee: float, initial_personal_balance: float, mode: CoverageMode) -> float:
    if mode == CoverageMode.GROW_DEPOSIT_BY_FEE:
        return initial_personal_balance + challenge_fee
    return challenge_fee
