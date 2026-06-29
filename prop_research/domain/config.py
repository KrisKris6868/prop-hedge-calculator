from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class StageConfig:
    name: str
    profit_target: float
    max_loss: float
    max_loss_mode: str = "amount"
    daily_loss: float | None = None
    daily_loss_mode: str = "amount"
    max_risk_per_trade: float | None = None
    drawdown_mode: str = "static"

    def __post_init__(self) -> None:
        if self.profit_target <= 0:
            raise ValueError("profit_target must be positive")
        if self.max_loss <= 0:
            raise ValueError("max_loss must be positive")
        if self.max_loss_mode not in {"amount", "percent"}:
            raise ValueError("max_loss_mode must be amount or percent")
        if self.daily_loss is not None and self.daily_loss <= 0:
            raise ValueError("daily_loss must be positive")
        if self.daily_loss_mode not in {"amount", "percent"}:
            raise ValueError("daily_loss_mode must be amount or percent")
        if self.max_risk_per_trade is not None and self.max_risk_per_trade <= 0:
            raise ValueError("max_risk_per_trade must be positive")
        if self.drawdown_mode not in {"static", "trailing"}:
            raise ValueError("drawdown_mode must be static or trailing")


@dataclass(frozen=True)
class FundedConfig:
    profit_target_for_first_payout: float
    max_loss: float
    trader_split: float
    max_loss_mode: str = "amount"
    daily_loss: float | None = None
    daily_loss_mode: str = "amount"
    max_risk_per_trade: float | None = None
    drawdown_mode: str = "static"

    def __post_init__(self) -> None:
        if self.profit_target_for_first_payout <= 0:
            raise ValueError("profit_target_for_first_payout must be positive")
        if self.max_loss <= 0:
            raise ValueError("max_loss must be positive")
        if not 0 < self.trader_split <= 1:
            raise ValueError("trader_split must be in (0, 1]")
        if self.max_loss_mode not in {"amount", "percent"}:
            raise ValueError("max_loss_mode must be amount or percent")
        if self.daily_loss is not None and self.daily_loss <= 0:
            raise ValueError("daily_loss must be positive")
        if self.daily_loss_mode not in {"amount", "percent"}:
            raise ValueError("daily_loss_mode must be amount or percent")
        if self.max_risk_per_trade is not None and self.max_risk_per_trade <= 0:
            raise ValueError("max_risk_per_trade must be positive")
        if self.drawdown_mode not in {"static", "trailing"}:
            raise ValueError("drawdown_mode must be static or trailing")


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
