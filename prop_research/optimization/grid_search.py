from __future__ import annotations

from dataclasses import dataclass

from prop_research.simulation.monte_carlo import MonteCarloEngine, SimulationConfig, SimulationSummary
from prop_research.strategies.fixed import FixedPersonalRiskStrategy


@dataclass(frozen=True)
class FixedRiskCandidate:
    risk_amount: float
    summary: SimulationSummary


@dataclass(frozen=True)
class FixedRiskOptimizationResult:
    candidates: list[FixedRiskCandidate]
    best: FixedRiskCandidate


class GridSearchOptimizer:
    def __init__(self, engine: MonteCarloEngine) -> None:
        self.engine = engine

    def optimize_fixed_risk(
        self,
        simulation: SimulationConfig,
        risk_amounts: list[float],
    ) -> FixedRiskOptimizationResult:
        if not risk_amounts:
            raise ValueError("risk_amounts must not be empty")

        candidates = [
            FixedRiskCandidate(
                risk_amount=risk_amount,
                summary=self.engine.run(
                    simulation=simulation,
                    strategy=FixedPersonalRiskStrategy(risk_amount=risk_amount),
                ).summary,
            )
            for risk_amount in risk_amounts
        ]
        best = max(candidates, key=lambda candidate: candidate.summary.expected_real_wealth)
        return FixedRiskOptimizationResult(candidates=candidates, best=best)

