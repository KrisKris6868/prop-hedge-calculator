from types import SimpleNamespace

from prop_research.app.streamlit_app import (
    _default_prop_risk_percent,
    _enabled_tab_labels,
    _funded_target_for_config,
    _hedge_summary_display,
    _make_prop_firm_config,
    _personal_spent,
    _positive_amount,
    _stage_options,
    _risk_percent_from_amount,
    _stage_risk_percent,
    _target_distance_display,
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


def test_target_distance_display_is_blank_when_target_is_disabled() -> None:
    assert _target_distance_display(enabled=False, distance=1_234.0) == ""
    assert _target_distance_display(enabled=True, distance=1_234.0) == "$1,234.00"


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

    assert _stage_options(config) == {"funded": "Instant счет"}


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
    assert not hasattr(config, "account_type")


def test_positive_amount_falls_back_from_stale_zero_widget_state() -> None:
    assert _positive_amount(0.0, fallback=8_000.0) == 8_000.0
    assert _positive_amount(-1.0, fallback=8_000.0) == 8_000.0
    assert _positive_amount(3_000.0, fallback=8_000.0) == 3_000.0
