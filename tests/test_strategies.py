from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig
from prop_research.domain.snapshot import StateSnapshot
from prop_research.strategies.continuous import ContinuousPersonalRiskStrategy
from prop_research.strategies.zoned import ZonedPersonalRiskStrategy


def make_snapshot(stage_pnl: float, personal_balance: float = 200.0) -> StateSnapshot:
    config = PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=[StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0)],
        funded=FundedConfig(profit_target_for_first_payout=5_000.0, max_loss=8_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
    )
    return StateSnapshot.initial(config=config, initial_personal_balance=personal_balance).with_stage_pnl(stage_pnl)


def test_zoned_strategy_uses_stronger_hedge_near_max_loss_than_near_target() -> None:
    strategy = ZonedPersonalRiskStrategy(
        near_loss_multiplier=0.08,
        mid_multiplier=0.04,
        near_target_multiplier=0.01,
    )

    near_loss = strategy.decide(make_snapshot(stage_pnl=-7_000.0))
    near_target = strategy.decide(make_snapshot(stage_pnl=5_500.0))

    assert near_loss.personal_risk_amount > near_target.personal_risk_amount
    assert near_loss.allow_personal_trade is True


def test_zoned_strategy_can_disable_hedge_near_target() -> None:
    strategy = ZonedPersonalRiskStrategy(near_target_multiplier=0.0)

    decision = strategy.decide(make_snapshot(stage_pnl=5_700.0))

    assert decision.personal_risk_amount == 0.0
    assert decision.allow_personal_trade is False


def test_continuous_strategy_increases_risk_when_prop_is_closer_to_max_loss() -> None:
    strategy = ContinuousPersonalRiskStrategy(min_multiplier=0.01, max_multiplier=0.08)

    healthy = strategy.decide(make_snapshot(stage_pnl=0.0))
    threatened = strategy.decide(make_snapshot(stage_pnl=-7_500.0))

    assert threatened.personal_risk_amount > healthy.personal_risk_amount


def test_continuous_strategy_decreases_risk_when_prop_is_close_to_target() -> None:
    strategy = ContinuousPersonalRiskStrategy(min_multiplier=0.01, max_multiplier=0.08)

    neutral = strategy.decide(make_snapshot(stage_pnl=0.0))
    near_target = strategy.decide(make_snapshot(stage_pnl=5_800.0))

    assert near_target.personal_risk_amount < neutral.personal_risk_amount
