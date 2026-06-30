from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig
from prop_research.domain.enums import CycleState, PropState
from prop_research.domain.state_machine import PropStateMachine
from prop_research.strategies.fixed import FixedPersonalRiskStrategy


def make_default_config() -> PropFirmConfig:
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


def test_prop_moves_to_phase_2_when_phase_1_target_is_reached() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=200.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(6):
        machine.apply_trade(prop_win=True)

    assert machine.snapshot.prop_state == PropState.CHALLENGE_PHASE
    assert machine.snapshot.stage_index == 1
    assert machine.snapshot.stage_pnl == 0.0


def test_prop_fails_when_challenge_max_loss_is_breached() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=0.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(8):
        machine.apply_trade(prop_win=False)

    assert machine.snapshot.prop_state == PropState.PROP_FAILED_BEFORE_PAYOUT
    assert machine.snapshot.cycle_state == CycleState.CYCLE_UNRECOVERABLE_FAILURE


def test_after_second_phase_target_machine_enters_funded_pre_payout() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=200.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(12):
        machine.apply_trade(prop_win=True)

    assert machine.snapshot.prop_state == PropState.FUNDED_PRE_PAYOUT
    assert machine.snapshot.cycle_state == CycleState.CYCLE_RUNNING


def test_first_payout_makes_cycle_success_and_ignores_negative_personal_balance() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=-10_000.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(17):
        machine.apply_trade(prop_win=True)

    assert machine.snapshot.prop_state == PropState.FIRST_PAYOUT_RECEIVED
    assert machine.snapshot.cycle_state == CycleState.CYCLE_SUCCESS


def test_failure_before_payout_is_recoverable_when_personal_covers_challenge_fee() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=200.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(8):
        machine.apply_trade(prop_win=False)

    assert machine.snapshot.cycle_state == CycleState.CYCLE_RECOVERABLE_FAILURE


def test_failure_before_payout_is_unrecoverable_when_personal_does_not_cover_fee() -> None:
    machine = PropStateMachine.start(
        config=make_default_config(),
        initial_personal_balance=199.99,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    for _ in range(8):
        machine.apply_trade(prop_win=False)

    assert machine.snapshot.cycle_state == CycleState.CYCLE_UNRECOVERABLE_FAILURE


def test_instant_account_starts_directly_before_first_payout() -> None:
    config = PropFirmConfig(
        challenge_fee=300.0,
        nominal_balance=100_000.0,
        stages=[],
        funded=FundedConfig(profit_target_for_first_payout=2_000.0, max_loss=3_000.0, trader_split=0.8),
        prop_risk_per_trade=1_000.0,
        account_type="instant",
    )

    machine = PropStateMachine.start(
        config=config,
        initial_personal_balance=300.0,
        strategy=FixedPersonalRiskStrategy(risk_amount=0.0),
    )

    assert machine.snapshot.prop_state == PropState.FUNDED_PRE_PAYOUT
    assert machine.snapshot.stage_index == 0
