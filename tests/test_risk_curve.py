from prop_research.app.risk_curve import build_risk_curve
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig
from prop_research.strategies.zoned import ZonedPersonalRiskStrategy


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


def test_risk_curve_uses_one_percent_prop_risk_as_dollar_base() -> None:
    rows = build_risk_curve(
        config=make_config(),
        strategy=ZonedPersonalRiskStrategy(mid_multiplier=0.04),
        stage_key="phase_1",
        personal_balance=200.0,
        prop_risk_percent=1.0,
        points=5,
    )

    neutral_row = rows[2]

    assert neutral_row["Риск пропа, $"] == 1000.0
    assert neutral_row["Риск личного счета, $"] == 40.0
    assert neutral_row["Риск личного / риск пропа, %"] == 4.0


def test_risk_curve_marks_funded_stage() -> None:
    rows = build_risk_curve(
        config=make_config(),
        strategy=ZonedPersonalRiskStrategy(funded_multiplier=0.02),
        stage_key="funded",
        personal_balance=200.0,
        prop_risk_percent=1.0,
        points=3,
    )

    assert rows[0]["Стадия"] == "Funded до первой выплаты"
    assert rows[1]["Риск личного счета, $"] == 20.0
