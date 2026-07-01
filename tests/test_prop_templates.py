from prop_research.config.templates import (
    delete_prop_template,
    load_prop_templates,
    prop_firm_from_template_config,
    prop_firm_to_template_config,
    save_prop_template,
)
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


def make_config(account_type: str = "challenge") -> PropFirmConfig:
    stages = [
        StageConfig(
            name="phase_1",
            profit_target=6_000.0,
            max_loss=8_000.0,
            daily_loss=4_000.0,
            max_risk_per_trade=1_900.0,
            drawdown_mode="static",
        ),
        StageConfig(
            name="phase_2",
            profit_target=5_000.0,
            max_loss=8_000.0,
            daily_loss=4_000.0,
            max_risk_per_trade=1_500.0,
            drawdown_mode="static",
        ),
    ]
    if account_type == "instant":
        stages = []
    return PropFirmConfig(
        challenge_fee=190.0,
        nominal_balance=100_000.0,
        stages=stages,
        funded=FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=14_000.0,
            daily_loss=3_000.0,
            max_risk_per_trade=1_000.0,
            trader_split=0.8,
            drawdown_mode="static",
        ),
        prop_risk_per_trade=1_900.0,
        account_type=account_type,
    )


def test_template_config_round_trip_preserves_account_rules() -> None:
    config = make_config()

    restored = prop_firm_from_template_config(prop_firm_to_template_config(config))

    assert restored == config


def test_save_load_and_overwrite_template(tmp_path) -> None:
    path = tmp_path / "templates.json"
    save_prop_template(
        path,
        name="ПипФарм 100к 2ф-35%",
        config=make_config(),
        ui_state={"funded_consistency_enabled": True, "funded_consistency": 35.0},
    )
    save_prop_template(
        path,
        name="ПипФарм 100к 2ф-35%",
        config=make_config("instant"),
        ui_state={"instant_consistency_enabled": True, "instant_consistency": 30.0},
    )

    templates = load_prop_templates(path)

    assert len(templates) == 1
    assert templates[0].name == "ПипФарм 100к 2ф-35%"
    assert prop_firm_from_template_config(templates[0].config).account_type == "instant"
    assert templates[0].ui_state == {"instant_consistency_enabled": True, "instant_consistency": 30.0}


def test_delete_template_removes_only_selected_name(tmp_path) -> None:
    path = tmp_path / "templates.json"
    save_prop_template(path, name="A", config=make_config(), ui_state={})
    save_prop_template(path, name="B", config=make_config("instant"), ui_state={})

    delete_prop_template(path, "A")

    assert [template.name for template in load_prop_templates(path)] == ["B"]
