from dataclasses import replace

from prop_research.app.trading_cockpit import (
    _consistency_state,
    _consistency_text,
    _execution_buffer_label,
    _execution_settings_changed,
    _funded_payout_values,
    _minimum_days_state,
    _minimum_days_text,
    _margin_settings_changed,
    _pnl_step_for_stage,
    _risk_input_value,
    _rule_amount_from_input,
    _trailing_line_text,
    apply_template_to_account_state,
    build_default_account_config,
    build_account_summary,
    create_account_state_from_config,
    create_account_state_from_template,
    preview_account_state,
    rename_account_state,
    reset_account_runtime,
)
from prop_research.config.account_states import AccountState
from prop_research.config.templates import PropTemplate, prop_firm_from_template_config, prop_firm_to_template_config
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


def make_config() -> PropFirmConfig:
    return PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[
            StageConfig(
                name="phase_1",
                profit_target=6_000.0,
                max_loss=8_000.0,
                daily_loss=4_000.0,
                max_risk_per_trade=1_900.0,
            ),
            StageConfig(
                name="phase_2",
                profit_target=5_000.0,
                max_loss=8_000.0,
                daily_loss=4_000.0,
                max_risk_per_trade=1_500.0,
            ),
        ],
        funded=FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=8_000.0,
            daily_loss=4_000.0,
            max_risk_per_trade=1_000.0,
            trader_split=0.8,
        ),
        prop_risk_per_trade=1_900.0,
        account_type="challenge",
    )


def test_build_account_summary_uses_saved_calculator_progress() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 1_900.0,
            "calculator_stop_points_phase_1": 100.0,
            "calculator_trade_risk_applied_phase_1": 1_900.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.name == "PipFarm 100k"
    assert summary.stage_key == "phase_1"
    assert summary.current_pnl == 1_900.0
    assert summary.prop_risk == 1_900.0
    assert summary.prop_lot == 19.0
    assert summary.hedge_lot > 0
    assert summary.distance_to_target == 4_100.0
    assert summary.initial_personal_balance > 0
    assert summary.prop_account_size == 100_000.0


def test_rule_amount_input_supports_percent_of_prop_balance() -> None:
    assert _rule_amount_from_input(8.0, "percent", 100_000.0) == 8_000.0
    assert _rule_amount_from_input(8_000.0, "amount", 100_000.0) == 8_000.0


def test_margin_extra_liquidity_updates_summary_status_when_saved() -> None:
    config = make_config()
    base_runtime = {
        "calculator_stage_key": "funded",
        "calculator_current_prop_pnl": 4_500.0,
        "calculator_trade_risk_applied_funded": 1_000.0,
        "calculator_stop_points_funded": 100.0,
        "hedge_margin_leverage_funded": 300.0,
        "hedge_margin_stop_out_funded": 50.0,
    }
    account_without_extra = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state=base_runtime,
    )
    account_with_extra = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={**base_runtime, "hedge_margin_extra_liquidity_funded": 100.0},
    )

    assert build_account_summary(account_without_extra).margin_topup > 0
    assert build_account_summary(account_with_extra).margin_topup == 0.0
    assert _margin_settings_changed(base_runtime, "funded", {"hedge_margin_extra_liquidity": 100.0})


def test_instant_economic_trailing_auto_caps_prop_risk_from_settings() -> None:
    config = build_default_account_config("Инстант")
    account = AccountState(
        name="Instant",
        config=prop_firm_to_template_config(config),
        ui_state={
            "instant_prop_risk_strategy": "Экономный trailing",
            "instant_consistency_enabled": True,
            "instant_consistency": 15.0,
        },
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_current_prop_pnl": 0.0,
            "calculator_trade_risk_applied_funded": 900.0,
            "calculator_stop_points_funded": 100.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.prop_risk == 750.0


def test_execution_buffer_increases_personal_risk_for_every_account_type() -> None:
    base_account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_phase_1": 100.0,
            "calculator_trade_risk_applied_phase_1": 1_000.0,
        },
    )
    buffered_account = AccountState(
        name=base_account.name,
        config=base_account.config,
        ui_state={"execution_buffer_mode": "normal_10"},
        runtime_state=base_account.runtime_state,
    )

    base = build_account_summary(base_account)
    buffered = build_account_summary(buffered_account)

    assert buffered.personal_risk == round(base.personal_risk * 1.10, 2)


def test_execution_buffer_is_converted_to_stop_points_automatically() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"execution_buffer_mode": "light_5"},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_phase_1": 160.0,
            "calculator_trade_risk_applied_phase_1": 1_000.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.hedge_lot == 0.16
    assert summary.base_personal_risk == 25.0
    assert summary.personal_risk == 26.28


def test_manual_execution_costs_are_added_on_top_of_auto_buffer_points() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"execution_buffer_mode": "light_5", "execution_spread_points": 10.0},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_phase_1": 100.0,
            "calculator_trade_risk_applied_phase_1": 1_000.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.hedge_lot == 0.25
    assert summary.base_personal_risk == 25.0
    assert summary.personal_risk == 28.75


def test_execution_costs_are_added_to_personal_risk_without_changing_lot() -> None:
    base_account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_phase_1": 100.0,
            "calculator_trade_risk_applied_phase_1": 1_000.0,
        },
    )
    costed_account = AccountState(
        name=base_account.name,
        config=base_account.config,
        ui_state={"execution_spread_points": 13.0, "execution_commission_per_lot": 7.0},
        runtime_state=base_account.runtime_state,
    )

    base = build_account_summary(base_account)
    costed = build_account_summary(costed_account)

    assert costed.hedge_lot == base.hedge_lot
    assert costed.base_personal_risk == base.base_personal_risk
    assert costed.personal_risk == round(base.personal_risk + base.hedge_lot * 20.0, 2)


def test_execution_settings_changed_ignores_missing_default_values() -> None:
    assert not _execution_settings_changed(
        {},
        {"execution_buffer_mode": "off", "execution_spread_points": 0.0, "execution_commission_per_lot": 0.0},
    )
    assert _execution_settings_changed({}, {"execution_buffer_mode": "light_5"})


def test_execution_buffer_label_is_shown_only_when_enabled() -> None:
    assert _execution_buffer_label({}) == ""
    assert _execution_buffer_label({"execution_buffer_mode": "off"}) == ""
    assert _execution_buffer_label({"execution_buffer_mode": "light_5"}) == " · buffer 5%"
    assert _execution_buffer_label({"execution_buffer_mode": "normal_10"}) == " · buffer 10%"


def test_create_account_state_from_template_starts_clean_path() -> None:
    template = PropTemplate(
        name="PipFarm 100k 2f-35%",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"phase_1_consistency_enabled": True, "phase_1_consistency": 35.0},
    )

    account = create_account_state_from_template("  Рабочий PipFarm  ", template)

    assert account.name == "Рабочий PipFarm"
    assert account.config == template.config
    assert account.ui_state == template.ui_state
    assert account.runtime_state["calculator_stage_key"] == "phase_1"
    assert account.runtime_state["calculator_previous_stage_key"] == "phase_1"
    assert account.runtime_state["calculator_current_prop_pnl"] == 0.0
    assert account.runtime_state["calculator_trade_risk_applied_phase_1"] == 1_900.0
    assert account.runtime_state["calculator_trade_journal_phase_1"] == []


def test_create_account_state_from_template_rejects_duplicate_name() -> None:
    template = PropTemplate(
        name="PipFarm 100k 2f-35%",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
    )
    existing = [
        AccountState(
            name="Рабочий PipFarm",
            config=template.config,
            ui_state={},
            runtime_state={},
        )
    ]

    try:
        create_account_state_from_template(" рабочий pipfarm ", template, existing_accounts=existing)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate account name was accepted")


def test_build_default_account_config_supports_one_phase_two_phase_and_instant() -> None:
    one_phase = build_default_account_config("1 фаза")
    two_phase = build_default_account_config("2 фазы")
    instant = build_default_account_config("Инстант")

    assert one_phase.account_type == "challenge"
    assert len(one_phase.stages) == 1
    assert two_phase.account_type == "challenge"
    assert len(two_phase.stages) == 2
    assert instant.account_type == "instant"
    assert instant.stages == []
    assert instant.funded.max_risk_per_trade == instant.prop_risk_per_trade


def test_create_account_state_from_config_starts_clean_path_without_template() -> None:
    config = build_default_account_config("Инстант")

    account = create_account_state_from_config(
        " Instant work ",
        config,
        ui_state={"instant_consistency_enabled": True, "instant_consistency": 30.0},
    )

    assert account.name == "Instant work"
    assert prop_firm_from_template_config(account.config) == config
    assert account.ui_state["instant_consistency"] == 30.0
    assert account.runtime_state["calculator_stage_key"] == "funded"
    assert account.runtime_state["calculator_current_prop_pnl"] == 0.0
    assert account.runtime_state["calculator_trade_journal_funded"] == []


def test_create_account_state_from_config_rejects_duplicate_name() -> None:
    config = build_default_account_config("2 фазы")
    existing = [create_account_state_from_config("Two phase", config)]

    try:
        create_account_state_from_config(" two phase ", config, existing_accounts=existing)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate account name was accepted")


def test_rename_account_state_preserves_settings_and_runtime() -> None:
    account = AccountState(
        name="Old name",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"phase_1_consistency_enabled": True},
        runtime_state={"calculator_stage_key": "phase_1", "calculator_current_prop_pnl": 1_000.0},
    )

    renamed = rename_account_state(account, "  New name  ")

    assert renamed.name == "New name"
    assert renamed.config == account.config
    assert renamed.ui_state == account.ui_state
    assert renamed.runtime_state == account.runtime_state


def test_rename_account_state_rejects_duplicate_name_except_itself() -> None:
    account = AccountState(
        name="Main account",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={},
    )
    existing = [
        account,
        AccountState(name="Second", config=account.config, ui_state={}, runtime_state={}),
    ]

    assert rename_account_state(account, " main account ", existing_accounts=existing).name == "main account"

    try:
        rename_account_state(account, "second", existing_accounts=existing)
    except ValueError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("duplicate account rename was accepted")


def test_apply_template_to_account_state_replaces_rules_and_resets_runtime() -> None:
    old_config = make_config()
    base_new_config = make_config()
    new_config = replace(
        base_new_config,
        nominal_balance=50_000.0,
        stages=[
            replace(base_new_config.stages[0], max_risk_per_trade=900.0),
            base_new_config.stages[1],
        ],
    )
    account = AccountState(
        name="Main account",
        config=prop_firm_to_template_config(old_config),
        ui_state={"phase_1_consistency_enabled": True},
        runtime_state={"calculator_stage_key": "phase_2", "calculator_current_prop_pnl": 3_000.0},
    )
    template = PropTemplate(
        name="New rules",
        config=prop_firm_to_template_config(new_config),
        ui_state={"phase_1_consistency_enabled": False},
    )

    updated = apply_template_to_account_state(account, template)

    assert updated.name == account.name
    assert updated.config == template.config
    assert updated.ui_state == template.ui_state
    assert updated.runtime_state["calculator_stage_key"] == "phase_1"
    assert updated.runtime_state["calculator_current_prop_pnl"] == 0.0
    assert updated.runtime_state["calculator_trade_risk_applied_phase_1"] == 900.0


def test_preview_account_state_recalculates_lots_without_mutating_saved_account() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 1_900.0,
            "calculator_stop_points_phase_1": 100.0,
            "calculator_trade_risk_applied_phase_1": 1_900.0,
        },
    )

    preview = preview_account_state(account, stage_key="phase_1", pnl=3_800.0, stop_points=140.0, risk=1_900.0)
    summary = build_account_summary(preview)

    assert account.runtime_state["calculator_current_prop_pnl"] == 1_900.0
    assert summary.current_pnl == 3_800.0
    assert summary.prop_lot == 13.57
    assert summary.distance_to_target == 2_200.0


def test_reset_account_runtime_keeps_settings_but_resets_path_progress() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"funded_consistency": 35.0},
        runtime_state={
            "calculator_stage_key": "phase_2",
            "calculator_current_prop_pnl": 3_800.0,
            "calculator_completed_personal_spent": 100.0,
            "calculator_largest_winning_trade_phase_1": 1_900.0,
            "calculator_trade_journal_phase_1": [{"pnl_delta": 1_900.0}],
            "calculator_trailing_high_watermark_phase_1": 3_800.0,
            "calculator_stop_points_phase_1": 140.0,
            "calculator_trade_risk_applied_phase_1": 1_900.0,
        },
    )

    reset = reset_account_runtime(account)

    assert reset.ui_state == account.ui_state
    assert reset.runtime_state["calculator_stage_key"] == "phase_1"
    assert reset.runtime_state["calculator_current_prop_pnl"] == 0.0
    assert reset.runtime_state["calculator_completed_personal_spent"] == 0.0
    assert reset.runtime_state["calculator_stop_points_phase_1"] == 140.0
    assert reset.runtime_state["calculator_trade_risk_applied_phase_1"] == 1_900.0
    assert reset.runtime_state["calculator_largest_winning_trade_phase_1"] == 0.0
    assert reset.runtime_state["calculator_trade_journal_phase_1"] == []


def test_summary_caps_prop_risk_to_remaining_target() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 5_600.0,
            "calculator_stop_points_phase_1": 150.0,
            "calculator_trade_risk_applied_phase_1": 1_900.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.prop_risk == 400.0
    assert summary.prop_lot == 2.67
    assert summary.distance_to_target == 400.0


def test_funded_next_summary_uses_new_cycle_balance_and_risk() -> None:
    account = AccountState(
        name="PipFarm funded next",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "funded_next",
            "calculator_current_prop_pnl": 0.0,
            "calculator_funded_next_start_balance": 500.0,
            "calculator_stop_points_funded_next": 100.0,
            "calculator_trade_risk_applied_funded_next": 1_000.0,
        },
    )

    summary = build_account_summary(account)

    assert summary.stage_key == "funded_next"
    assert summary.initial_personal_balance == 500.0
    assert summary.personal_balance == 500.0
    assert summary.base_personal_risk == 125.0
    assert summary.personal_spent == 0.0


def test_risk_input_value_never_goes_below_streamlit_minimum() -> None:
    assert _risk_input_value(0.0) == 1.0
    assert _risk_input_value(float("nan")) == 1.0
    assert _risk_input_value(500.0) == 500.0


def test_preview_caps_pnl_to_stage_target() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_phase_1": 150.0,
            "calculator_trade_risk_applied_phase_1": 1_900.0,
        },
    )

    preview = preview_account_state(account, stage_key="phase_1", pnl=7_600.0, stop_points=150.0, risk=1_900.0)
    summary = build_account_summary(preview)

    assert preview.runtime_state["calculator_current_prop_pnl"] == 6_000.0
    assert summary.current_pnl == 6_000.0
    assert summary.status == "2-я фаза"


def test_preview_caps_pnl_to_stage_max_loss() -> None:
    config = build_default_account_config("Инстант")
    account = AccountState(
        name="Instant",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_current_prop_pnl": 0.0,
            "calculator_stop_points_funded": 100.0,
            "calculator_trade_risk_applied_funded": 900.0,
        },
    )

    preview = preview_account_state(account, stage_key="funded", pnl=-6_750.0, stop_points=100.0, risk=900.0)
    summary = build_account_summary(preview)

    assert preview.runtime_state["calculator_current_prop_pnl"] == -6_000.0
    assert summary.current_pnl == -6_000.0
    assert summary.distance_to_max_loss == 0.0


def test_preview_records_pnl_change_as_stage_trade_and_largest_win() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={"phase_1_consistency_enabled": True, "phase_1_consistency": 35.0},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 4_500.0,
            "calculator_stop_points_phase_1": 150.0,
            "calculator_trade_risk_applied_phase_1": 1_000.0,
            "calculator_largest_winning_trade_phase_1": 0.0,
        },
    )

    preview = preview_account_state(account, stage_key="phase_1", pnl=5_500.0, stop_points=150.0, risk=1_000.0)

    assert preview.runtime_state["calculator_trade_journal_phase_1"][-1]["pnl_delta"] == 1_000.0
    assert preview.runtime_state["calculator_largest_winning_trade_phase_1"] == 1_000.0
    assert _consistency_text(preview, "phase_1", 5_500.0) != "Сделок не было"
    assert _consistency_text(preview, "phase_1", 5_500.0) == "max $1,000.00<br>35%"
    assert _consistency_state(preview, "phase_1", 5_500.0) == "ok"


def test_preview_records_losing_pnl_change_without_increasing_largest_win() -> None:
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(make_config()),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "phase_1",
            "calculator_current_prop_pnl": 4_500.0,
            "calculator_largest_winning_trade_phase_1": 1_000.0,
        },
    )

    preview = preview_account_state(account, stage_key="phase_1", pnl=3_500.0, stop_points=150.0, risk=1_000.0)

    assert preview.runtime_state["calculator_trade_journal_phase_1"][-1]["pnl_delta"] == -1_000.0
    assert preview.runtime_state["calculator_largest_winning_trade_phase_1"] == 1_000.0


def test_pnl_step_uses_prop_risk_until_target_remainder() -> None:
    config = make_config()

    assert _pnl_step_for_stage(config, "phase_1", current_pnl=1_000.0, risk=1_900.0) == 1_900.0
    assert _pnl_step_for_stage(config, "phase_1", current_pnl=5_500.0, risk=1_900.0) == 500.0


def test_consistency_text_is_dash_when_disabled_and_plain_when_no_trades() -> None:
    config = make_config()
    base = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={"calculator_stage_key": "phase_1"},
    )

    assert _consistency_text(base, "phase_1", 0.0) == "—"

    enabled = AccountState(
        name=base.name,
        config=base.config,
        ui_state={"phase_1_consistency_enabled": True, "phase_1_consistency": 35.0},
        runtime_state={"calculator_stage_key": "phase_1", "calculator_largest_winning_trade_phase_1": 0.0},
    )

    assert _consistency_text(enabled, "phase_1", 0.0) == "Сделок не было"


def test_consistency_text_falls_back_to_current_positive_pnl_for_legacy_state() -> None:
    config = make_config()
    account = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={"phase_1_consistency_enabled": True, "phase_1_consistency": 35.0},
        runtime_state={"calculator_stage_key": "phase_1", "calculator_largest_winning_trade_phase_1": 0.0},
    )

    assert _consistency_text(account, "phase_1", 5_500.0) == "max $5,500.00<br>35%"
    assert _consistency_state(account, "phase_1", 5_500.0) == "warn"


def test_minimum_days_text_is_compact_fraction_or_dash() -> None:
    config = make_config()
    base = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={"calculator_stage_key": "funded"},
    )

    assert _minimum_days_text(base, config, "funded", 0.0) == "—"

    enabled_without_trades = AccountState(
        name=base.name,
        config=base.config,
        ui_state={
            "minimum_profitable_days_enabled": True,
            "minimum_profitable_days_required": 5,
            "minimum_profitable_day_percent": 0.5,
        },
        runtime_state={"calculator_stage_key": "funded"},
    )

    assert _minimum_days_text(enabled_without_trades, config, "funded", 0.0) == "0/5"
    assert _minimum_days_text(enabled_without_trades, config, "funded", 2_500.0) == "0/5"

    enabled_with_trades = AccountState(
        name=base.name,
        config=base.config,
        ui_state=enabled_without_trades.ui_state,
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_trade_journal_funded": [
                {"pnl_delta": 2_500.0},
                {"pnl_delta": 400.0},
                {"pnl_delta": -800.0},
                {"pnl_delta": 600.0},
            ],
        },
    )

    assert _minimum_days_text(enabled_with_trades, config, "funded", 2_700.0) == "2/5"
    assert _minimum_days_state("5/5") == "ok"


def test_trailing_line_text_shows_high_watermark_and_loss_line() -> None:
    config = build_default_account_config("Инстант")
    account = AccountState(
        name="Instant",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_current_prop_pnl": 900.0,
            "calculator_trailing_high_watermark_funded": 900.0,
        },
    )

    assert _trailing_line_text(account, config, "funded", 900.0) == "Trailing max $100,900.00 · линия слива $94,900.00"


def test_preview_keeps_trailing_high_watermark_after_pnl_pullback() -> None:
    config = build_default_account_config("Инстант")
    account = AccountState(
        name="Instant",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_current_prop_pnl": 1_500.0,
            "calculator_trailing_high_watermark_funded": 1_500.0,
        },
    )

    preview = preview_account_state(account, stage_key="funded", pnl=1_000.0, stop_points=100.0, risk=500.0)

    assert preview.runtime_state["calculator_trailing_high_watermark_funded"] == 1_500.0
    assert _trailing_line_text(preview, config, "funded", 1_000.0) == "Trailing max $101,500.00 · линия слива $95,500.00"


def test_preview_records_new_trailing_high_watermark() -> None:
    config = build_default_account_config("Инстант")
    account = AccountState(
        name="Instant",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={
            "calculator_stage_key": "funded",
            "calculator_current_prop_pnl": 0.0,
        },
    )

    preview = preview_account_state(account, stage_key="funded", pnl=1_500.0, stop_points=100.0, risk=500.0)

    assert preview.runtime_state["calculator_trailing_high_watermark_funded"] == 1_500.0


def test_funded_payout_values_show_split_net_and_cleanest() -> None:
    config = make_config()

    profit, split, net, cleanest = _funded_payout_values(
        config=config,
        stage_key="funded",
        current_pnl=5_000.0,
        personal_spent=500.0,
    )

    assert profit == 5_000.0
    assert split == 4_000.0
    assert net == 3_500.0
    assert cleanest == 3_300.0
