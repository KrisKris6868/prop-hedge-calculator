from prop_research.app.hedge_model import (
    CoverageMode,
    build_dealing_instruction,
    build_stage_plan,
    calculate_personal_balance_from_prop_pnl,
    calculate_personal_risk_for_trade,
    minimum_personal_deposit_for_strict_free_prop,
    required_risk_at_point,
)
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


def make_config() -> PropFirmConfig:
    return PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0),
            StageConfig(name="phase_2", profit_target=6_000.0, max_loss=8_000.0),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )


def test_required_risk_to_grow_personal_deposit_by_challenge_fee_from_phase_start() -> None:
    result = required_risk_at_point(
        challenge_fee=200.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        current_prop_pnl=0.0,
        max_loss=8_000.0,
        prop_risk_amount=1_000.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert result.required_personal_risk == 25.0
    assert result.loss_trades_to_failure == 8.0


def test_required_risk_to_only_keep_enough_for_next_challenge_accounts_for_current_balance() -> None:
    result = required_risk_at_point(
        challenge_fee=200.0,
        initial_personal_balance=200.0,
        current_personal_balance=50.0,
        current_prop_pnl=0.0,
        max_loss=8_000.0,
        prop_risk_amount=1_000.0,
        mode=CoverageMode.BALANCE_COVERS_NEXT_CHALLENGE,
    )

    assert result.required_personal_risk == 18.75


def test_strict_grow_deposit_model_is_not_feasible_with_two_phases_and_funded() -> None:
    plan = build_stage_plan(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert plan.rows[0].required_personal_risk == 25.0
    assert plan.rows[1].required_personal_risk == 43.75
    assert round(plan.rows[2].required_personal_risk, 2) == 76.56
    assert plan.feasible_with_initial_deposit is False
    assert plan.personal_loss_if_all_targets_hit > 200.0


def test_next_challenge_coverage_model_is_feasible_with_initial_challenge_fee_deposit() -> None:
    plan = build_stage_plan(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.BALANCE_COVERS_NEXT_CHALLENGE,
    )

    assert plan.feasible_with_initial_deposit is True
    assert plan.personal_loss_if_all_targets_hit == 0.0


def test_minimum_personal_deposit_for_strict_free_prop() -> None:
    requirement = minimum_personal_deposit_for_strict_free_prop(
        config=make_config(),
        prop_risk_percent=1.0,
    )

    assert requirement.minimum_personal_deposit == 795.31
    assert requirement.total_capital_before_payout == 995.31
    assert requirement.total_success_path_personal_loss == 795.31


def test_dealing_instruction_for_one_and_half_percent_prop_risk() -> None:
    instruction = build_dealing_instruction(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.5,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    first = instruction[0]
    second = instruction[1]
    funded = instruction[2]

    assert first["Стадия"] == "Этап 1: phase_1"
    assert first["Риск пропа, $"] == 1500.0
    assert first["Риск личного, $"] == 37.5
    assert first["Риск личного, % от проп-риска"] == 2.5
    assert first["Личная просадка при проходе стадии, $"] == 150.0

    assert second["Риск личного, $"] == 65.62
    assert second["Личный счет после прохода стадии, $"] == -212.48

    assert funded["Риск личного, $"] == 114.84
    assert funded["Личный счет при потере пропа, $"] == 400.0


def test_trade_calculator_returns_single_personal_risk_for_current_stage() -> None:
    result = calculate_personal_risk_for_trade(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=0.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=1.5,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert result["Риск пропа, $"] == 1500.0
    assert result["Риск личного, $"] == 37.5
    assert result["Если на пропе Long"] == "на личном Short"
    assert result["Если на пропе Short"] == "на личном Long"


def test_trade_calculator_increases_personal_risk_when_prop_is_closer_to_failure() -> None:
    neutral = calculate_personal_risk_for_trade(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=0.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )
    near_failure = calculate_personal_risk_for_trade(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=-6_000.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert near_failure["Риск личного, $"] > neutral["Риск личного, $"]


def test_personal_balance_is_derived_from_prop_pnl_on_current_stage() -> None:
    result = calculate_personal_balance_from_prop_pnl(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=3_000.0,
        initial_personal_balance=200.0,
        prop_risk_percent=1.5,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert result["Старт личного счета на стадии, $"] == 200.0
    assert result["Текущий баланс личного счета, $"] == 125.0
    assert result["Изменение личного счета на стадии, $"] == -75.0


def test_personal_balance_includes_previous_stage_losses() -> None:
    result = calculate_personal_balance_from_prop_pnl(
        config=make_config(),
        stage_key="phase_2",
        current_prop_pnl=-3_000.0,
        initial_personal_balance=200.0,
        prop_risk_percent=1.5,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert result["Старт личного счета на стадии, $"] == 50.0
    assert result["Текущий баланс личного счета, $"] == 181.24
    assert result["Изменение личного счета на стадии, $"] == 131.24
