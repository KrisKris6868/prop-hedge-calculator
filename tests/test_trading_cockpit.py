from prop_research.app.trading_cockpit import (
    _consistency_text,
    _funded_payout_values,
    _minimum_days_state,
    _minimum_days_text,
    _pnl_step_for_stage,
    build_account_summary,
    preview_account_state,
    reset_account_runtime,
)
from prop_research.config.account_states import AccountState
from prop_research.config.templates import prop_firm_to_template_config
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

    assert _consistency_text(account, "phase_1", 5_500.0) != "Сделок не было"


def test_minimum_days_text_is_compact_fraction_or_dash() -> None:
    config = make_config()
    base = AccountState(
        name="PipFarm 100k",
        config=prop_firm_to_template_config(config),
        ui_state={},
        runtime_state={"calculator_stage_key": "funded"},
    )

    assert _minimum_days_text(base, config, "funded", 0.0) == "—"

    enabled = AccountState(
        name=base.name,
        config=base.config,
        ui_state={
            "minimum_profitable_days_enabled": True,
            "minimum_profitable_days_required": 5,
            "minimum_profitable_day_percent": 0.5,
        },
        runtime_state={"calculator_stage_key": "funded"},
    )

    assert _minimum_days_text(enabled, config, "funded", 0.0) == "0/5"
    assert _minimum_days_text(enabled, config, "funded", 2_500.0) == "5/5"
    assert _minimum_days_state("5/5") == "ok"


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
