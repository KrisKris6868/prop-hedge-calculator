from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig
from prop_research.optimization.grid_search import GridSearchOptimizer
from prop_research.simulation.monte_carlo import MonteCarloEngine, SimulationConfig
from prop_research.strategies.fixed import FixedPersonalRiskStrategy


def make_config() -> PropFirmConfig:
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


def test_monte_carlo_is_reproducible_with_same_seed() -> None:
    engine = MonteCarloEngine()
    simulation = SimulationConfig(
        prop_firm=make_config(),
        initial_personal_balance=200.0,
        win_probability=0.55,
        max_trades_per_cycle=100,
        runs=50,
        seed=42,
    )
    strategy = FixedPersonalRiskStrategy(risk_amount=20.0)

    first = engine.run(simulation=simulation, strategy=strategy)
    second = engine.run(simulation=simulation, strategy=strategy)

    assert first.summary == second.summary


def test_monte_carlo_reports_success_and_recoverable_failure_probabilities() -> None:
    engine = MonteCarloEngine()
    simulation = SimulationConfig(
        prop_firm=make_config(),
        initial_personal_balance=200.0,
        win_probability=0.5,
        max_trades_per_cycle=50,
        runs=25,
        seed=7,
    )

    result = engine.run(simulation=simulation, strategy=FixedPersonalRiskStrategy(risk_amount=0.0))

    assert 0.0 <= result.summary.probability_of_first_payout <= 1.0
    assert 0.0 <= result.summary.probability_of_recoverable_failure <= 1.0
    assert 0.0 <= result.summary.probability_of_unrecoverable_failure <= 1.0


def test_optimizer_selects_strategy_by_expected_real_wealth() -> None:
    simulation = SimulationConfig(
        prop_firm=make_config(),
        initial_personal_balance=200.0,
        win_probability=0.6,
        max_trades_per_cycle=80,
        runs=40,
        seed=11,
    )
    optimizer = GridSearchOptimizer(engine=MonteCarloEngine())

    result = optimizer.optimize_fixed_risk(
        simulation=simulation,
        risk_amounts=[0.0, 10.0, 20.0],
    )

    expected_best = max(result.candidates, key=lambda candidate: candidate.summary.expected_real_wealth)
    assert result.best.risk_amount == expected_best.risk_amount
