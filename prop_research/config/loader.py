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
            )
            for stage in raw["stages"]
        ],
        funded=FundedConfig(
            profit_target_for_first_payout=float(raw["funded"]["profit_target_for_first_payout"]),
            max_loss=float(raw["funded"]["max_loss"]),
            trader_split=float(raw["funded"]["trader_split"]),
        ),
    )

