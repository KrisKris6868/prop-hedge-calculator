from __future__ import annotations

from dataclasses import dataclass
from random import Random

from prop_research.domain.config import PropFirmConfig
from prop_research.domain.enums import CycleState
from prop_research.domain.snapshot import StateSnapshot
from prop_research.domain.state_machine import PropStateMachine
from prop_research.strategies.base import PersonalRiskStrategy


@dataclass(frozen=True)
class SimulationConfig:
    prop_firm: PropFirmConfig
    initial_personal_balance: float
    win_probability: float
    max_trades_per_cycle: int
    runs: int
    seed: int

    def __post_init__(self) -> None:
        if not 0 <= self.win_probability <= 1:
            raise ValueError("win_probability must be in [0, 1]")
        if self.max_trades_per_cycle <= 0:
            raise ValueError("max_trades_per_cycle must be positive")
        if self.runs <= 0:
            raise ValueError("runs must be positive")


@dataclass(frozen=True)
class SimulationSummary:
    expected_real_wealth: float
    probability_of_first_payout: float
    probability_of_recoverable_failure: float
    probability_of_unrecoverable_failure: float
    average_external_topup: float
    max_external_topup_before_payout: float


@dataclass(frozen=True)
class SimulationResult:
    runs: list[StateSnapshot]
    summary: SimulationSummary


class MonteCarloEngine:
    def run(self, simulation: SimulationConfig, strategy: PersonalRiskStrategy) -> SimulationResult:
        rng = Random(simulation.seed)
        snapshots: list[StateSnapshot] = []

        for _ in range(simulation.runs):
            machine = PropStateMachine.start(
                config=simulation.prop_firm,
                initial_personal_balance=simulation.initial_personal_balance,
                strategy=strategy,
            )
            for _trade_index in range(simulation.max_trades_per_cycle):
                if machine.snapshot.cycle_state != CycleState.CYCLE_RUNNING:
                    break
                machine.apply_trade(prop_win=rng.random() < simulation.win_probability)
            snapshots.append(machine.snapshot)

        return SimulationResult(runs=snapshots, summary=self._summarize(snapshots))

    def _summarize(self, snapshots: list[StateSnapshot]) -> SimulationSummary:
        count = len(snapshots)
        successes = sum(1 for item in snapshots if item.cycle_state == CycleState.CYCLE_SUCCESS)
        recoverable = sum(1 for item in snapshots if item.cycle_state == CycleState.CYCLE_RECOVERABLE_FAILURE)
        unrecoverable = sum(1 for item in snapshots if item.cycle_state == CycleState.CYCLE_UNRECOVERABLE_FAILURE)
        wealth = sum(item.final_wealth for item in snapshots) / count
        topups = [item.external_topups_paid for item in snapshots]

        return SimulationSummary(
            expected_real_wealth=wealth,
            probability_of_first_payout=successes / count,
            probability_of_recoverable_failure=recoverable / count,
            probability_of_unrecoverable_failure=unrecoverable / count,
            average_external_topup=sum(topups) / count,
            max_external_topup_before_payout=max(topups),
        )

