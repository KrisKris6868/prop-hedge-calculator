from __future__ import annotations

import json
from pathlib import Path

from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


def load_prop_firm_config(path: str | Path) -> PropFirmConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return PropFirmConfig(
        challenge_fee=float(raw["challenge_fee"]),
        nominal_balance=float(raw["nominal_balance"]),
        prop_risk_per_trade=float(raw["prop_risk_per_trade"]),
        stages=[
            StageConfig(
                name=str(stage["name"]),
                profit_target=float(stage["profit_target"]),
                max_loss=float(stage["max_loss"]),
                max_loss_mode=str(stage.get("max_loss_mode", "amount")),
                daily_loss=float(stage["daily_loss"]) if stage.get("daily_loss") is not None else None,
                daily_loss_mode=str(stage.get("daily_loss_mode", "amount")),
                max_risk_per_trade=float(stage["max_risk_per_trade"])
                if stage.get("max_risk_per_trade") is not None
                else None,
                drawdown_mode=str(stage.get("drawdown_mode", "static")),
            )
            for stage in raw["stages"]
        ],
        funded=FundedConfig(
            profit_target_for_first_payout=float(raw["funded"]["profit_target_for_first_payout"]),
            max_loss=float(raw["funded"]["max_loss"]),
            trader_split=float(raw["funded"]["trader_split"]),
            max_loss_mode=str(raw["funded"].get("max_loss_mode", "amount")),
            daily_loss=float(raw["funded"]["daily_loss"]) if raw["funded"].get("daily_loss") is not None else None,
            daily_loss_mode=str(raw["funded"].get("daily_loss_mode", "amount")),
            max_risk_per_trade=float(raw["funded"]["max_risk_per_trade"])
            if raw["funded"].get("max_risk_per_trade") is not None
            else None,
            drawdown_mode=str(raw["funded"].get("drawdown_mode", "static")),
        ),
        account_type=str(raw.get("account_type", "challenge")),
    )
