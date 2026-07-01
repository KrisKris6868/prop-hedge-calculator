from prop_research.app.trading_cockpit import build_account_summary
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
