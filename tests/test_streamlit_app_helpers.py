from types import SimpleNamespace

from prop_research.app.streamlit_app import (
    _cap_prop_pnl_to_target,
    _consistency_status_display,
    _consistency_state_keys,
    _economic_trailing_prop_risk,
    _default_prop_risk_percent,
    _enabled_tab_labels,
    _funded_target_for_config,
    _funded_continuation_cycle,
    _funded_next_cycle_result,
    _hedge_margin_liquidity,
    _liquidity_inputs_for_margin,
    _liquidity_personal_risk_for_margin,
    _synced_broker_deposit,
    _lot_from_risk_and_stop_points,
    _hedge_multiple_display,
    _hedge_summary_display,
    _make_prop_firm_config,
    _money,
    _minimum_profitable_days_status_display,
    _next_stage_key,
    _next_stage_label,
    _pnl_after_stage_change,
    _personal_risk_with_execution_buffer,
    _profitable_days_from_pnl,
    _prop_risk_for_strategy,
    _trade_risk_input_value,
    _personal_spent,
    _positive_amount,
    _stage_options,
    _stage_profit_target,
    _risk_percent_from_amount,
    _stage_risk_percent,
    _target_distance_display,
    _total_personal_spent,
    _trailing_drawdown_display,
    _updated_largest_winning_trade,
)
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


def make_config() -> PropFirmConfig:
    return PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0, max_risk_per_trade=1_600.0),
        ],
        funded=FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=8_000.0,
            trader_split=0.8,
            max_risk_per_trade=1_000.0,
        ),
        prop_risk_per_trade=1_000.0,
    )


def test_default_prop_risk_percent_comes_from_first_stage_max_risk() -> None:
    assert _default_prop_risk_percent(make_config()) == 1.6


def test_stage_risk_percent_comes_from_selected_stage_max_risk() -> None:
    assert _stage_risk_percent(make_config(), "phase_1") == 1.6
    assert _stage_risk_percent(make_config(), "funded") == 1.0


def test_disabled_funded_target_keeps_real_config_value_instead_of_nominal_balance() -> None:
    assert _funded_target_for_config(
        enabled=False,
        input_value=5_000.0,
        existing_value=5_000.0,
        nominal_balance=100_000.0,
    ) == 5_000.0


def test_risk_percent_from_amount_uses_current_trade_risk_amount() -> None:
    assert _risk_percent_from_amount(3_000.0, 100_000.0) == 3.0


def test_lot_from_risk_and_stop_points_uses_5_digit_points() -> None:
    assert _lot_from_risk_and_stop_points(risk_amount=50.0, stop_points_5_digit=150.0) == 0.33
    assert _lot_from_risk_and_stop_points(risk_amount=1_300.0, stop_points_5_digit=100.0) == 13.0
    assert _lot_from_risk_and_stop_points(risk_amount=50.0, stop_points_5_digit=0.0) == 0.0


def test_hedge_multiple_display_keeps_only_multiplier() -> None:
    assert _hedge_multiple_display(11.4) == "11.4x"
    assert _hedge_multiple_display(40.0) == "40x"
    assert _hedge_multiple_display(0.0) == "нет хеджа"


def test_target_distance_display_is_blank_when_target_is_disabled() -> None:
    assert _target_distance_display(enabled=False, distance=1_234.0) == ""
    assert _target_distance_display(enabled=True, distance=1_234.0) == "$1,234.00"


def test_stage_profit_target_comes_from_selected_stage() -> None:
    assert _stage_profit_target(make_config(), "phase_1") == 6_000.0
    assert _stage_profit_target(make_config(), "funded") == 5_000.0


def test_cap_prop_pnl_to_target_stops_overshoot_on_last_click() -> None:
    assert _cap_prop_pnl_to_target(7_600.0, target_enabled=True, profit_target=6_000.0) == 6_000.0
    assert _cap_prop_pnl_to_target(5_700.0, target_enabled=True, profit_target=6_000.0) == 5_700.0
    assert _cap_prop_pnl_to_target(7_600.0, target_enabled=False, profit_target=6_000.0) == 7_600.0


def test_pnl_after_stage_change_resets_challenge_stage() -> None:
    assert _pnl_after_stage_change(
        account_type="challenge",
        previous_stage_key="phase_1",
        current_stage_key="phase_2",
        current_prop_pnl=6_000.0,
    ) == 0.0
    assert _pnl_after_stage_change(
        account_type="challenge",
        previous_stage_key="phase_2",
        current_stage_key="phase_2",
        current_prop_pnl=1_900.0,
    ) == 1_900.0
    assert _pnl_after_stage_change(
        account_type="instant",
        previous_stage_key="funded",
        current_stage_key="funded",
        current_prop_pnl=2_000.0,
    ) == 2_000.0


def test_funded_continuation_cycle_targets_one_percent_profit_on_failure() -> None:
    result = _funded_continuation_cycle(
        nominal_balance=100_000.0,
        max_loss=8_000.0,
        profit_target=5_000.0,
        prop_risk=1_000.0,
        personal_balance_after_payout=500.0,
        protection_percent=1.0,
    )

    assert result["Цель прибыли личного при сливе, $"] == 1_000.0
    assert result["Риск личного на сделку, $"] == 125.0
    assert result["Депозит на следующий цикл, $"] == 625.0
    assert result["Нужно докинуть, $"] == 125.0
    assert result["Личный баланс при сливе, $"] == 1_625.0


def test_funded_next_cycle_result_does_not_subtract_old_challenge_costs() -> None:
    result = _funded_next_cycle_result(
        current_prop_pnl=-7_500.0,
        current_personal_balance=1_437.5,
        funded_next_start_balance=500.0,
        trader_split=0.8,
    )

    assert result["Профит на funded, $"] == 0.0
    assert result["К выплате после сплита, $"] == 0.0
    assert result["Результат hedge, $"] == 937.5
    assert result["Итог цикла, $"] == 937.5


def test_hedge_margin_liquidity_calculates_lot_margin_and_topup() -> None:
    enough = _hedge_margin_liquidity(
        personal_risk=125.0,
        stop_points_5_digit=100.0,
        leverage=300.0,
        eurusd_price=1.14,
        broker_deposit=755.0,
        spread_points_5_digit=0.0,
        commission_per_million_per_side=10.0,
        stop_out_percent=50.0,
    )
    tight = _hedge_margin_liquidity(
        personal_risk=125.0,
        stop_points_5_digit=100.0,
        leverage=300.0,
        eurusd_price=1.14,
        broker_deposit=125.0,
        spread_points_5_digit=0.0,
        commission_per_million_per_side=10.0,
        stop_out_percent=50.0,
    )

    assert enough["Лот hedge"] == 1.23
    assert enough["Маржа нужна, $"] == 465.69
    assert enough["Критический equity Stop Out, $"] == 232.84
    assert enough["Equity после стопа, $"] == 630.0
    assert enough["Запас до Stop Out после стопа, $"] == 397.16
    assert enough["Комиссия+спред, $"] == 2.45
    assert enough["Докинуть под маржу, $"] == 0.0
    assert enough["Докинуть чтобы стоп выдержал, $"] == 0.0
    assert tight["Докинуть под маржу, $"] == 340.69
    assert tight["Докинуть чтобы стоп выдержал, $"] == 232.84


def test_liquidity_personal_risk_keeps_last_real_risk_after_target_reached() -> None:
    assert (
        _liquidity_personal_risk_for_margin(
            target_reached=True,
            buffered_personal_risk=113.75,
            next_personal_risk=0.0,
        )
        == 113.75
    )
    assert (
        _liquidity_personal_risk_for_margin(
            target_reached=False,
            buffered_personal_risk=113.75,
            next_personal_risk=113.75,
        )
        == 113.75
    )


def test_liquidity_inputs_use_remembered_working_state_after_target_reached() -> None:
    current_inputs = {
        "personal_risk": float("inf"),
        "stop_points_5_digit": 100.0,
        "leverage": 300.0,
        "eurusd_price": 1.14,
        "broker_deposit": 645.31,
        "spread_points_5_digit": 0.0,
        "commission_per_million_per_side": 10.0,
        "stop_out_percent": 50.0,
    }
    remembered_inputs = {
        "personal_risk": 108.33,
        "stop_points_5_digit": 100.0,
        "leverage": 300.0,
        "eurusd_price": 1.14,
        "broker_deposit": 755.55,
        "spread_points_5_digit": 0.0,
        "commission_per_million_per_side": 10.0,
        "stop_out_percent": 50.0,
    }

    assert (
        _liquidity_inputs_for_margin(
            target_reached=True,
            current_inputs=current_inputs,
            remembered_inputs=remembered_inputs,
        )
        == remembered_inputs
    )
    assert (
        _liquidity_inputs_for_margin(
            target_reached=False,
            current_inputs=current_inputs,
            remembered_inputs=remembered_inputs,
        )
        == current_inputs
    )


def test_synced_broker_deposit_uses_personal_balance_plus_liquidity() -> None:
    assert _synced_broker_deposit(current_personal_balance=755.55, extra_liquidity=100.0) == 855.55
    assert _synced_broker_deposit(current_personal_balance=755.55, extra_liquidity=-100.0) == 755.55


def test_trailing_drawdown_display_shows_high_watermark_and_failure_line() -> None:
    assert _trailing_drawdown_display(
        nominal_balance=100_000.0,
        trailing_high_watermark=1_000.0,
        max_loss=5_000.0,
    ) == "Trailing max $101,000.00 · линия слива $96,000.00"


def test_consistency_status_display_shows_needed_profit() -> None:
    assert _consistency_status_display(
        enabled=True,
        rule_percent=15.0,
        current_prop_pnl=4_500.0,
        largest_profit=900.0,
    ) == (
        "warning",
        "Consistency еще не выполнен: нужен PnL $6,000.00, осталось $1,500.00.",
    )
    assert _consistency_status_display(
        enabled=True,
        rule_percent=15.0,
        current_prop_pnl=6_000.0,
        largest_profit=900.0,
    ) == (
        "success",
        "Consistency выполнен: крупнейшая сделка $900.00 укладывается в 15.00% от прибыли.",
    )


def test_consistency_status_display_is_hidden_when_disabled() -> None:
    assert _consistency_status_display(
        enabled=False,
        rule_percent=15.0,
        current_prop_pnl=4_500.0,
        largest_profit=900.0,
    ) is None


def test_consistency_state_keys_cover_instant_phases_and_funded() -> None:
    assert _consistency_state_keys("instant", "funded") == ("instant_consistency_enabled", "instant_consistency")
    assert _consistency_state_keys("challenge", "phase_1") == ("phase_1_consistency_enabled", "phase_1_consistency")
    assert _consistency_state_keys("challenge", "phase_2") == ("phase_2_consistency_enabled", "phase_2_consistency")
    assert _consistency_state_keys("challenge", "funded") == ("funded_consistency_enabled", "funded_consistency")


def test_minimum_profitable_days_status_display_tracks_required_days() -> None:
    assert _minimum_profitable_days_status_display(
        enabled=True,
        required_days=5,
        completed_days=2,
        minimum_day_profit=500.0,
    ) == (
        "warning",
        "Минимальные прибыльные дни: выполнено 2/5. Нужно еще 3 дня минимум по $500.00.",
    )
    assert _minimum_profitable_days_status_display(
        enabled=True,
        required_days=5,
        completed_days=5,
        minimum_day_profit=500.0,
    ) == (
        "success",
        "Минимальные прибыльные дни выполнены: 5/5 дней минимум по $500.00.",
    )


def test_minimum_profitable_days_status_display_is_hidden_when_disabled() -> None:
    assert _minimum_profitable_days_status_display(
        enabled=False,
        required_days=5,
        completed_days=2,
        minimum_day_profit=500.0,
    ) is None


def test_profitable_days_are_counted_automatically_from_pnl() -> None:
    assert _profitable_days_from_pnl(current_prop_pnl=499.0, minimum_day_profit=500.0, required_days=5) == 0
    assert _profitable_days_from_pnl(current_prop_pnl=900.0, minimum_day_profit=500.0, required_days=5) == 1
    assert _profitable_days_from_pnl(current_prop_pnl=2_600.0, minimum_day_profit=500.0, required_days=5) == 5
    assert _profitable_days_from_pnl(current_prop_pnl=-900.0, minimum_day_profit=500.0, required_days=5) == 0


def test_largest_winning_trade_is_remembered_until_reset() -> None:
    remembered = _updated_largest_winning_trade(
        previous_largest=0.0,
        current_prop_pnl=900.0,
        current_trade_prop_risk=900.0,
    )
    assert remembered == 900.0
    assert _updated_largest_winning_trade(
        previous_largest=remembered,
        current_prop_pnl=900.0,
        current_trade_prop_risk=600.0,
    ) == 900.0
    assert _updated_largest_winning_trade(
        previous_largest=remembered,
        current_prop_pnl=-900.0,
        current_trade_prop_risk=1_200.0,
    ) == 900.0


def test_economic_trailing_uses_full_risk_when_prop_is_negative() -> None:
    risk, reason = _economic_trailing_prop_risk(
        max_risk_per_trade=900.0,
        nominal_balance=100_000.0,
        current_prop_pnl=-500.0,
        profit_target=5_000.0,
        consistency_enabled=True,
        consistency_percent=15.0,
        minimum_days_enabled=True,
        minimum_day_percent=0.5,
        minimum_days_required=5,
    )

    assert risk == 900.0
    assert "минус" in reason


def test_economic_trailing_caps_start_risk_by_consistency() -> None:
    risk, reason = _economic_trailing_prop_risk(
        max_risk_per_trade=900.0,
        nominal_balance=100_000.0,
        current_prop_pnl=0.0,
        profit_target=5_000.0,
        consistency_enabled=True,
        consistency_percent=15.0,
        minimum_days_enabled=True,
        minimum_day_percent=0.5,
        minimum_days_required=5,
    )

    assert risk == 750.0
    assert "consistency" in reason


def test_economic_trailing_uses_min_day_risk_until_days_are_done() -> None:
    risk, reason = _economic_trailing_prop_risk(
        max_risk_per_trade=900.0,
        nominal_balance=100_000.0,
        current_prop_pnl=1_000.0,
        profit_target=5_000.0,
        consistency_enabled=True,
        consistency_percent=15.0,
        minimum_days_enabled=True,
        minimum_day_percent=0.5,
        minimum_days_required=5,
    )

    assert risk == 500.0
    assert "дни" in reason


def test_economic_trailing_switches_to_finish_risk_after_days_are_done() -> None:
    risk, reason = _economic_trailing_prop_risk(
        max_risk_per_trade=900.0,
        nominal_balance=100_000.0,
        current_prop_pnl=2_700.0,
        profit_target=5_000.0,
        consistency_enabled=True,
        consistency_percent=15.0,
        minimum_days_enabled=True,
        minimum_day_percent=0.5,
        minimum_days_required=5,
    )

    assert risk == 250.0
    assert "защит" in reason.lower()


def test_prop_risk_for_strategy_applies_economic_recommendation_automatically() -> None:
    assert _prop_risk_for_strategy("Экономный trailing", manual_risk=900.0, recommended_risk=500.0) == 500.0
    assert _prop_risk_for_strategy("Вручную", manual_risk=900.0, recommended_risk=500.0) == 900.0


def test_trade_risk_input_value_shows_economic_recommendation() -> None:
    assert _trade_risk_input_value("Экономный trailing", manual_risk=900.0, recommended_risk=500.0) == 500.0
    assert _trade_risk_input_value("Вручную", manual_risk=900.0, recommended_risk=500.0) == 900.0


def test_personal_risk_with_execution_buffer_adds_selected_percent() -> None:
    assert _personal_risk_with_execution_buffer(33.54, "off") == 33.54
    assert _personal_risk_with_execution_buffer(33.54, "normal_10") == 36.89
    assert _personal_risk_with_execution_buffer(33.54, "safety_15") == 38.57


def test_personal_spent_shows_only_used_personal_funds() -> None:
    assert _personal_spent(starting_balance=795.31, current_balance=705.31) == 90.0
    assert _personal_spent(starting_balance=795.31, current_balance=840.31) == 0.0


def test_hedge_summary_display_is_compact() -> None:
    assert _hedge_summary_display(multiplier=40.0, personal_percent=2.5) == "2.50% от пропа · 40x меньше"
    assert _hedge_summary_display(multiplier=0.0, personal_percent=0.0) == "нет хеджа"


def test_stage_options_for_instant_account_only_show_instant() -> None:
    config = PropFirmConfig(
        challenge_fee=300.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(profit_target_for_first_payout=2_000.0, max_loss=3_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    assert _stage_options(config) == {"funded": "Instant счет", "funded_next": "Funded после выплаты"}


def test_next_stage_key_moves_challenge_forward_only() -> None:
    stage_options = {"phase_1": "Phase 1", "phase_2": "Phase 2", "funded": "Funded", "funded_next": "Funded next"}

    assert _next_stage_key("challenge", "phase_1", stage_options) == "phase_2"
    assert _next_stage_key("challenge", "phase_2", stage_options) == "funded"
    assert _next_stage_key("challenge", "funded", stage_options) == "funded_next"
    assert _next_stage_key("challenge", "funded_next", stage_options) is None
    assert _next_stage_key("instant", "funded", {"funded": "Instant", "funded_next": "Funded next"}) is None


def test_next_stage_label_is_user_friendly() -> None:
    assert _next_stage_label("phase_2") == "2-я фаза"
    assert _next_stage_label("funded") == "Funded"
    assert _next_stage_label("funded_next") == "Funded после выплаты"
    assert _next_stage_label(None) == "Цель достигнута"


def test_total_personal_spent_sums_completed_and_current_stage() -> None:
    assert _total_personal_spent(completed_spent=180.48, current_stage_spent=52.515) == 233.0


def test_removed_tabs_are_not_rendered() -> None:
    assert _enabled_tab_labels() == [
        "Калькулятор сделки",
        "Симуляции / Monte Carlo",
        "Принципы выбора пропа",
    ]


def test_stage_options_are_backward_compatible_with_old_config_objects() -> None:
    config = SimpleNamespace(
        stages=[
            StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0),
        ],
    )

    assert _stage_options(config) == {
        "phase_1": "Этап 1: phase_1",
        "funded": "Funded до первой выплаты",
        "funded_next": "Funded после выплаты",
    }


def test_make_prop_firm_config_is_backward_compatible_without_account_type(monkeypatch) -> None:
    class LegacyPropFirmConfig:
        def __init__(self, challenge_fee, nominal_balance, stages, funded, prop_risk_per_trade):
            self.challenge_fee = challenge_fee
            self.nominal_balance = nominal_balance
            self.stages = stages
            self.funded = funded
            self.prop_risk_per_trade = prop_risk_per_trade

    import prop_research.app.streamlit_app as streamlit_app

    monkeypatch.setattr(streamlit_app, "PropFirmConfig", LegacyPropFirmConfig)

    config = _make_prop_firm_config(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0)],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
        account_type="challenge",
    )

    assert isinstance(config, LegacyPropFirmConfig)
    assert config.account_type == "challenge"


def test_make_prop_firm_config_keeps_instant_usable_with_legacy_config(monkeypatch) -> None:
    class LegacyPropFirmConfig:
        def __init__(self, challenge_fee, nominal_balance, stages, funded, prop_risk_per_trade):
            if not stages:
                raise ValueError("stages must not be empty")
            self.challenge_fee = challenge_fee
            self.nominal_balance = nominal_balance
            self.stages = stages
            self.funded = funded
            self.prop_risk_per_trade = prop_risk_per_trade

    import prop_research.app.streamlit_app as streamlit_app

    monkeypatch.setattr(streamlit_app, "PropFirmConfig", LegacyPropFirmConfig)

    config = _make_prop_firm_config(
        challenge_fee=300.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(
            profit_target_for_first_payout=2_000.0,
            max_loss=5_000.0,
            trader_split=0.8,
            daily_loss=4_000.0,
            max_risk_per_trade=1_000.0,
            drawdown_mode="static",
        ),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    assert isinstance(config, LegacyPropFirmConfig)
    assert config.account_type == "instant"
    assert len(config.stages) == 1
    assert config.stages[0].name == "instant"
    assert config.stages[0].profit_target == 2_000.0
    assert config.stages[0].max_loss == 5_000.0


def test_make_funded_config_recovers_from_zero_max_loss(monkeypatch) -> None:
    import prop_research.app.streamlit_app as streamlit_app

    original_funded_config = streamlit_app.FundedConfig
    attempts = {"count": 0}

    def flaky_funded_config(**kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise ValueError("max_loss must be positive")
        return original_funded_config(**kwargs)

    monkeypatch.setattr(streamlit_app, "FundedConfig", flaky_funded_config)

    funded = streamlit_app._make_funded_config(
        profit_target_for_first_payout=0.0,
        max_loss=0.0,
        trader_split=0.0,
        daily_loss=0.0,
        max_risk_per_trade=0.0,
    )

    assert funded.max_loss > 0
    assert funded.profit_target_for_first_payout > 0
    assert funded.trader_split > 0


def test_positive_amount_falls_back_from_stale_zero_widget_state() -> None:
    assert _positive_amount(0.0, fallback=8_000.0) == 8_000.0
    assert _positive_amount(-1.0, fallback=8_000.0) == 8_000.0
    assert _positive_amount(0.0, fallback=0.0) == 1.0
    assert _positive_amount(3_000.0, fallback=8_000.0) == 3_000.0


def test_money_hides_infinite_values() -> None:
    assert _money(float("inf")) == "недоступно"
    assert _money(float("-inf")) == "недоступно"
