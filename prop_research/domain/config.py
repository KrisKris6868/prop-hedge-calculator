from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageConfig:
    name: str
    profit_target: float
    max_loss: float

    def __post_init__(self) -> None:
        if self.profit_target <= 0:
            raise ValueError("profit_target must be positive")
        if self.max_loss <= 0:
            raise ValueError("max_loss must be positive")


@dataclass(frozen=True)
class FundedConfig:
    profit_target_for_first_payout: float
    max_loss: float
    trader_split: float

    def __post_init__(self) -> None:
        if self.profit_target_for_first_payout <= 0:
            raise ValueError("profit_target_for_first_payout must be positive")
        if self.max_loss <= 0:
            raise ValueError("max_loss must be positive")
        if not 0 < self.trader_split <= 1:
            raise ValueError("trader_split must be in (0, 1]")


@dataclass(frozen=True)
class PropFirmConfig:
    challenge_fee: float
    nominal_balance: float
    stages: list[StageConfig]
    funded: FundedConfig
    prop_risk_per_trade: float

    def __post_init__(self) -> None:
        if self.challenge_fee <= 0:
            raise ValueError("challenge_fee must be positive")
        if self.nominal_balance <= 0:
            raise ValueError("nominal_balance must be positive")
        if not self.stages:
            raise ValueError("at least one challenge stage is required")
        if self.prop_risk_per_trade <= 0:
            raise ValueError("prop_risk_per_trade must be positive")

