from prop_research.app.hedge_model import (
    CoverageMode,
    TrailingRiskMode,
    build_dealing_instruction,
    build_stage_plan,
    calculate_effective_prop_risk,
    calculate_personal_balance_from_prop_pnl,
    calculate_personal_risk_for_trade,
    calculate_funded_payout_preview,
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


def test_minimum_personal_deposit_excludes_funded_when_not_hedged() -> None:
    requirement = minimum_personal_deposit_for_strict_free_prop(
        config=make_config(),
        prop_risk_percent=1.0,
        hedge_funded=False,
    )

    assert requirement.minimum_personal_deposit == 412.5
    assert requirement.total_success_path_personal_loss == 412.5


def test_minimum_personal_deposit_recalculates_for_one_phase_challenge() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )

    requirement = minimum_personal_deposit_for_strict_free_prop(
        config=config,
        prop_risk_percent=1.0,
    )

    assert requirement.minimum_personal_deposit == 368.75
    assert requirement.total_capital_before_payout == 568.75


def test_minimum_personal_deposit_accounts_for_trailing_failure_after_high_watermark() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(
                name="phase_1",
                profit_target=6_000.0,
                max_loss=8_000.0,
                drawdown_mode="trailing",
            ),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )

    requirement = minimum_personal_deposit_for_strict_free_prop(
        config=config,
        prop_risk_percent=1.0,
        hedge_funded=False,
    )

    assert requirement.minimum_personal_deposit == 600.0


def test_stage_plan_accounts_for_trailing_failure_after_high_watermark() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(
                name="phase_1",
                profit_target=6_000.0,
                max_loss=8_000.0,
                drawdown_mode="trailing",
            ),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )

    plan = build_stage_plan(
        config=config,
        initial_personal_balance=600.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    row = plan.rows[0]
    assert row.required_personal_risk == 100.0
    assert row.personal_balance_after_stage_passed == 0.0
    assert row.personal_balance_if_stage_failed == 800.0


def test_target_lock_trailing_mode_reduces_instant_deposit_before_target_is_fixed() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=8_000.0,
            trader_split=0.8,
            max_risk_per_trade=1_000.0,
            drawdown_mode="trailing",
        ),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    requirement = minimum_personal_deposit_for_strict_free_prop(
        config=config,
        prop_risk_percent=1.0,
        trailing_risk_mode=TrailingRiskMode.TARGET_LOCK,
    )
    trade = calculate_personal_risk_for_trade(
        config=config,
        stage_key="funded",
        current_prop_pnl=0.0,
        initial_personal_balance=125.0,
        current_personal_balance=125.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        max_risk_per_trade=1_000.0,
        trailing_high_watermark=0.0,
        trailing_risk_mode=TrailingRiskMode.TARGET_LOCK,
    )

    assert requirement.minimum_personal_deposit == 125.0
    assert trade["Риск личного, $"] == 25.0
    assert trade["Loss до потери пропа"] == 8.0


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


def test_funded_instruction_includes_net_payout_after_personal_costs() -> None:
    instruction = build_dealing_instruction(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    funded = instruction[2]

    assert funded["Gross profit до выплаты, $"] == 5000.0
    assert funded["Профит сплит, %"] == 80.0
    assert funded["К выплате после сплита, $"] == 4000.0
    assert funded["Затраты личного счета до выплаты, $"] == 795.3
    assert funded["Чистыми после личных затрат, $"] == 3204.7


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


def test_personal_balance_can_ignore_pnl_that_happened_before_hedge_started() -> None:
    result = calculate_personal_balance_from_prop_pnl(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=-4_000.0,
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        include_current_prop_pnl=False,
    )

    values = list(result.values())
    assert values[4] == 200.0
    assert values[3] == 0.0


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


def test_funded_payout_preview_uses_current_funded_profit() -> None:
    preview = calculate_funded_payout_preview(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        funded_profit=1_000.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert preview["Профит на funded, $"] == 1000.0
    assert preview["Профит сплит, %"] == 80.0
    assert preview["К выплате после сплита, $"] == 800.0
    assert preview["Затраты личного счета до текущего funded profit, $"] == 489.06
    assert preview["Чистыми после личных затрат, $"] == 310.94


def test_effective_prop_risk_caps_trade_by_remaining_target() -> None:
    assert calculate_effective_prop_risk(
        max_risk_per_trade=1_900.0,
        distance_to_target=300.0,
        distance_to_max_loss=13_700.0,
    ) == 300.0


def test_effective_prop_risk_ignores_target_when_target_is_disabled() -> None:
    assert calculate_effective_prop_risk(
        max_risk_per_trade=1_900.0,
        distance_to_target=None,
        distance_to_max_loss=13_700.0,
    ) == 1_900.0


def test_trade_calculator_uses_trailing_high_watermark_for_max_loss_distance() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(
                name="phase_1",
                profit_target=6_000.0,
                max_loss=4_000.0,
                max_risk_per_trade=1_000.0,
                drawdown_mode="trailing",
            ),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=4_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )

    result = calculate_personal_risk_for_trade(
        config=config,
        stage_key="phase_1",
        current_prop_pnl=1_000.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        max_risk_per_trade=1_000.0,
        trailing_high_watermark=1_000.0,
    )

    assert result["distance_to_max_loss"] == 4_000.0


def test_trade_calculator_uses_trailing_recovery_distance_before_target() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=8_000.0,
            trader_split=0.8,
            max_risk_per_trade=1_000.0,
            drawdown_mode="trailing",
        ),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    result = calculate_personal_risk_for_trade(
        config=config,
        stage_key="funded",
        current_prop_pnl=0.0,
        initial_personal_balance=333.33,
        current_personal_balance=333.33,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        max_risk_per_trade=1_000.0,
        trailing_high_watermark=0.0,
    )

    assert result["Риск личного, $"] == 66.67
    assert result["Ожидаемый личный счет при потере пропа, $"] == 533.33


def test_trade_calculator_uses_effective_risk_near_profit_target() -> None:
    result = calculate_personal_risk_for_trade(
        config=make_config(),
        stage_key="phase_1",
        current_prop_pnl=5_700.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=1.9,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert result["full_prop_risk_amount"] == 1900.0
    assert result["effective_prop_risk_amount"] == 300.0
    assert result["distance_to_target"] == 300.0


def test_trade_calculator_explicit_trade_risk_overrides_stage_default() -> None:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0, max_risk_per_trade=1_900.0),
        ],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )

    result = calculate_personal_risk_for_trade(
        config=config,
        stage_key="phase_1",
        current_prop_pnl=0.0,
        initial_personal_balance=200.0,
        current_personal_balance=200.0,
        prop_risk_percent=3.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        max_risk_per_trade=3_000.0,
    )

    assert result["Риск пропа, $"] == 3_000.0
    assert result["effective_prop_risk_amount"] == 3_000.0


def test_instant_account_stage_plan_has_no_challenge_phases() -> None:
    config = PropFirmConfig(
        challenge_fee=300.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(profit_target_for_first_payout=2_000.0, max_loss=3_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    plan = build_stage_plan(
        config=config,
        initial_personal_balance=300.0,
        prop_risk_percent=1.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
    )

    assert [row.stage_name for row in plan.rows] == ["Instant счет"]
    assert plan.rows[0].profit_target == 2_000.0


def test_funded_payout_preview_can_exclude_funded_hedge_costs() -> None:
    preview = calculate_funded_payout_preview(
        config=make_config(),
        initial_personal_balance=200.0,
        prop_risk_percent=1.0,
        funded_profit=1_000.0,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        hedge_funded=False,
    )

    assert preview["Затраты личного счета до текущего funded profit, $"] == 412.5
    assert preview["Чистыми после личных затрат, $"] == 387.5
