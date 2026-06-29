import json

from prop_research.config.loader import load_prop_firm_config


def test_load_prop_firm_config_from_json(tmp_path) -> None:
    config_path = tmp_path / "firm.json"
    config_path.write_text(
        json.dumps(
            {
                "challenge_fee": 200,
                "nominal_balance": 100000,
                "prop_risk_per_trade": 1000,
                "stages": [
                    {"name": "phase_1", "profit_target": 6000, "max_loss": 8000},
                    {"name": "phase_2", "profit_target": 6000, "max_loss": 8000},
                ],
                "funded": {
                    "profit_target_for_first_payout": 5000,
                    "max_loss": 8000,
                    "trader_split": 0.8,
                },
            }
        ),
        encoding="utf-8",
    )

    config = load_prop_firm_config(config_path)

    assert config.challenge_fee == 200
    assert config.nominal_balance == 100000
    assert len(config.stages) == 2
    assert config.funded.trader_split == 0.8
