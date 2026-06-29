from prop_research.app.streamlit_app import (
    _default_prop_risk_percent,
    _funded_target_for_config,
    _stage_risk_percent,
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
