from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prop_research.app.hedge_model import (
    CoverageMode,
    build_dealing_instruction,
    build_stage_plan,
    calculate_funded_payout_preview,
    calculate_personal_balance_from_prop_pnl,
    calculate_personal_risk_for_trade,
    minimum_personal_deposit_for_strict_free_prop,
)
from prop_research.app.risk_curve import build_risk_curve
from prop_research.config.loader import load_prop_firm_config
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig
from prop_research.optimization.grid_search import GridSearchOptimizer
from prop_research.simulation.monte_carlo import MonteCarloEngine, SimulationConfig
from prop_research.strategies.continuous import ContinuousPersonalRiskStrategy
from prop_research.strategies.fixed import FixedPersonalRiskStrategy
from prop_research.strategies.zoned import ZonedPersonalRiskStrategy


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Prop Hedge Calculator", layout="wide")
    st.title("Калькулятор хеджа проп-счета")

    if st.sidebar.button("Сбросить настройки", use_container_width=True):
        st.session_state.clear()
        st.rerun()

    with st.sidebar.expander("Расширенные настройки", expanded=False):
        config_path = st.text_input("Файл правил", "configs/example_prop_firm.json")

    prop_firm = load_prop_firm_config(Path(config_path))
    prop_firm = _sidebar_rules(st, prop_firm)

    max_challenge_risk = max(stage.max_risk_per_trade or prop_firm.nominal_balance for stage in prop_firm.stages)
    max_funded_risk = prop_firm.funded.max_risk_per_trade or prop_firm.nominal_balance
    max_prop_risk_percent = max(max_challenge_risk, max_funded_risk) / prop_firm.nominal_balance * 100

    st.sidebar.subheader("Риск и личный счет")
    prop_risk_percent = st.sidebar.number_input(
        "Риск пропа до лимитов, %",
        value=min(1.0, float(max_prop_risk_percent)),
        min_value=0.01,
        max_value=float(max_prop_risk_percent),
        step=0.1,
    )
    recommended_balance = minimum_personal_deposit_for_strict_free_prop(
        config=prop_firm,
        prop_risk_percent=prop_risk_percent,
    ).minimum_personal_deposit
    st.sidebar.metric("Рекомендуемый личный депозит", _money(recommended_balance))
    use_recommended_balance = st.sidebar.checkbox("Использовать рекомендуемый депозит", value=True)
    custom_personal_balance = st.sidebar.number_input(
        "Начальный личный депозит, $",
        value=float(recommended_balance),
        min_value=0.0,
        step=10.0,
        disabled=use_recommended_balance,
    )
    initial_personal_balance = float(recommended_balance if use_recommended_balance else custom_personal_balance)
    coverage_label = st.sidebar.radio(
        "При потере пропа",
        [
            "Личный депозит растет на цену челленджа",
            "На личном хватает на новый челлендж",
        ],
    )
    coverage_mode = (
        CoverageMode.GROW_DEPOSIT_BY_FEE
        if coverage_label == "Личный депозит растет на цену челленджа"
        else CoverageMode.BALANCE_COVERS_NEXT_CHALLENGE
    )
    hedge_funded = not st.sidebar.checkbox("Не хеджировать funded", value=False)

    with st.sidebar.expander("Новости", expanded=False):
        consider_news = st.checkbox("Учитывать новости", value=False)
        forced_close_r = st.number_input("Forced close result, R", value=0.0, step=0.1, disabled=not consider_news)

    stage_options = {
        **{f"phase_{index + 1}": f"Этап {index + 1}: {stage.name}" for index, stage in enumerate(prop_firm.stages)},
        "funded": "Funded до первой выплаты",
    }

    calculator_tab, simulation_tab, research_tab, instant_tab, prop_vs_prop_tab, principles_tab = st.tabs(
        [
            "Калькулятор сделки",
            "Симуляции / Monte Carlo",
            "Исследования",
            "Инстант счета",
            "Prop vs Prop",
            "Принципы выбора пропа",
        ]
    )

    with calculator_tab:
        _render_trade_calculator(
            st=st,
            pd=pd,
            prop_firm=prop_firm,
            stage_options=stage_options,
            initial_personal_balance=initial_personal_balance,
            recommended_balance=recommended_balance,
            prop_risk_percent=prop_risk_percent,
            coverage_mode=coverage_mode,
            hedge_funded=hedge_funded,
            consider_news=consider_news,
            forced_close_r=forced_close_r,
        )

    with simulation_tab:
        _render_monte_carlo(
            st=st,
            prop_firm=prop_firm,
            initial_personal_balance=initial_personal_balance,
            prop_risk_percent=prop_risk_percent,
        )

    with research_tab:
        _render_research(
            st=st,
            pd=pd,
            prop_firm=prop_firm,
            stage_options=stage_options,
            initial_personal_balance=initial_personal_balance,
            prop_risk_percent=prop_risk_percent,
            coverage_mode=coverage_mode,
            hedge_funded=hedge_funded,
        )

    with instant_tab:
        _render_instant_accounts(st)

    with prop_vs_prop_tab:
        _render_prop_vs_prop(st, prop_firm)

    with principles_tab:
        _render_prop_selection_principles(st)


def _sidebar_rules(st, prop_firm: PropFirmConfig) -> PropFirmConfig:
    st.sidebar.subheader("Счет и челлендж")
    challenge_fee = st.sidebar.number_input("Цена челленджа, $", value=prop_firm.challenge_fee, min_value=1.0, step=10.0)
    nominal_balance = st.sidebar.number_input(
        "Размер проп-счета, $",
        value=prop_firm.nominal_balance,
        min_value=1_000.0,
        step=1_000.0,
    )

    st.sidebar.subheader("Challenge")
    challenge_type = st.sidebar.radio("Тип челленджа", ["1 фазный", "2 фазный"], horizontal=True)
    stages: list[StageConfig] = []
    source_stages = prop_firm.stages if len(prop_firm.stages) > 1 else [prop_firm.stages[0], prop_firm.stages[0]]
    for index, default_stage in enumerate(source_stages[: 2 if challenge_type == "2 фазный" else 1], start=1):
        with st.sidebar.expander(f"Этап {index}", expanded=index == 1):
            profit_target = st.number_input(
                f"Этап {index}: profit target, $",
                value=default_stage.profit_target,
                min_value=1.0,
                step=500.0,
                key=f"phase_{index}_target",
            )
            max_loss_mode = st.radio(
                f"Этап {index}: max loss режим",
                ["amount", "percent"],
                index=0 if default_stage.max_loss_mode == "amount" else 1,
                horizontal=True,
                key=f"phase_{index}_max_loss_mode",
            )
            max_loss_input = st.number_input(
                f"Этап {index}: max loss",
                value=_from_amount(default_stage.max_loss, max_loss_mode, prop_firm.nominal_balance),
                min_value=0.01,
                step=500.0 if max_loss_mode == "amount" else 0.1,
                key=f"phase_{index}_max_loss",
            )
            daily_loss_mode = st.radio(
                f"Этап {index}: daily loss режим",
                ["amount", "percent"],
                index=0 if default_stage.daily_loss_mode == "amount" else 1,
                horizontal=True,
                key=f"phase_{index}_daily_loss_mode",
            )
            default_daily_loss = default_stage.daily_loss or default_stage.max_loss / 2
            daily_loss_input = st.number_input(
                f"Этап {index}: daily loss",
                value=_from_amount(default_daily_loss, daily_loss_mode, prop_firm.nominal_balance),
                min_value=0.01,
                step=500.0 if daily_loss_mode == "amount" else 0.1,
                key=f"phase_{index}_daily_loss",
            )
            max_risk = st.number_input(
                f"Этап {index}: max risk per trade, $",
                value=float(default_stage.max_risk_per_trade or prop_firm.prop_risk_per_trade),
                min_value=1.0,
                step=100.0,
                key=f"phase_{index}_max_risk",
            )
            drawdown_mode = st.radio(
                f"Этап {index}: drawdown",
                ["static", "trailing"],
                index=0 if default_stage.drawdown_mode == "static" else 1,
                horizontal=True,
                key=f"phase_{index}_drawdown",
            )
            stages.append(
                StageConfig(
                    name=f"phase_{index}",
                    profit_target=float(profit_target),
                    max_loss=_to_amount(float(max_loss_input), max_loss_mode, prop_firm.nominal_balance),
                    max_loss_mode=max_loss_mode,
                    daily_loss=_to_amount(float(daily_loss_input), daily_loss_mode, prop_firm.nominal_balance),
                    daily_loss_mode=daily_loss_mode,
                    max_risk_per_trade=float(max_risk),
                    drawdown_mode=drawdown_mode,
                )
            )

    st.sidebar.subheader("Funded")
    funded = prop_firm.funded
    funded_profit_target_enabled = st.sidebar.checkbox("Funded profit target", value=True)
    funded_profit_target = st.sidebar.number_input(
        "Funded: profit target, $",
        value=funded.profit_target_for_first_payout,
        min_value=1.0,
        step=500.0,
        disabled=not funded_profit_target_enabled,
    )
    funded_max_loss_mode = st.sidebar.radio(
        "Funded: max loss режим",
        ["amount", "percent"],
        index=0 if funded.max_loss_mode == "amount" else 1,
        horizontal=True,
    )
    funded_max_loss = st.sidebar.number_input(
        "Funded: max loss",
        value=_from_amount(funded.max_loss, funded_max_loss_mode, prop_firm.nominal_balance),
        min_value=0.01,
        step=500.0 if funded_max_loss_mode == "amount" else 0.1,
    )
    funded_daily_loss_mode = st.sidebar.radio(
        "Funded: daily loss режим",
        ["amount", "percent"],
        index=0 if funded.daily_loss_mode == "amount" else 1,
        horizontal=True,
    )
    funded_daily_loss = st.sidebar.number_input(
        "Funded: daily loss",
        value=_from_amount(funded.daily_loss or funded.max_loss / 2, funded_daily_loss_mode, prop_firm.nominal_balance),
        min_value=0.01,
        step=500.0 if funded_daily_loss_mode == "amount" else 0.1,
    )
    funded_max_risk = st.sidebar.number_input(
        "Funded: max risk per trade, $",
        value=float(funded.max_risk_per_trade or prop_firm.prop_risk_per_trade),
        min_value=1.0,
        step=100.0,
    )
    trader_split_percent = st.sidebar.number_input(
        "Profit split трейдера, %",
        value=funded.trader_split * 100,
        min_value=1.0,
        max_value=100.0,
        step=1.0,
    )
    funded_drawdown_mode = st.sidebar.radio(
        "Funded: drawdown",
        ["static", "trailing"],
        index=0 if funded.drawdown_mode == "static" else 1,
        horizontal=True,
    )
    if funded_drawdown_mode == "trailing":
        st.sidebar.caption("Trailing drawdown требует state machine и будет учитываться в симуляции отдельным шагом.")

    return PropFirmConfig(
        challenge_fee=float(challenge_fee),
        nominal_balance=float(nominal_balance),
        stages=stages,
        funded=FundedConfig(
            profit_target_for_first_payout=float(funded_profit_target if funded_profit_target_enabled else nominal_balance),
            max_loss=_to_amount(float(funded_max_loss), funded_max_loss_mode, float(nominal_balance)),
            trader_split=float(trader_split_percent) / 100,
            max_loss_mode=funded_max_loss_mode,
            daily_loss=_to_amount(float(funded_daily_loss), funded_daily_loss_mode, float(nominal_balance)),
            daily_loss_mode=funded_daily_loss_mode,
            max_risk_per_trade=float(funded_max_risk),
            drawdown_mode=funded_drawdown_mode,
        ),
        prop_risk_per_trade=prop_firm.prop_risk_per_trade,
    )


def _render_trade_calculator(
    st,
    pd,
    prop_firm: PropFirmConfig,
    stage_options: dict[str, str],
    initial_personal_balance: float,
    recommended_balance: float,
    prop_risk_percent: float,
    coverage_mode: CoverageMode,
    hedge_funded: bool,
    consider_news: bool,
    forced_close_r: float,
) -> None:
    stage_plan = build_stage_plan(prop_firm, initial_personal_balance, prop_risk_percent, coverage_mode)
    top_1, top_2, top_3 = st.columns(3)
    top_1.metric("Начальный личный депозит", _money(initial_personal_balance))
    top_2.metric("Рекомендуемый личный депозит", _money(recommended_balance))
    top_3.metric("Цена челленджа", _money(prop_firm.challenge_fee))

    input_1, input_2 = st.columns(2)
    stage_key = input_1.selectbox("Текущая стадия", list(stage_options.keys()), format_func=stage_options.get)
    max_risk = _stage_max_risk(prop_firm, stage_key)
    current_prop_pnl_abs = input_2.number_input(
        "Текущий PnL пропа, $",
        value=0.0,
        step=float(max_risk),
    )
    is_drawdown = input_2.checkbox("Это просадка", value=False)
    current_prop_pnl = -abs(current_prop_pnl_abs) if is_drawdown else abs(current_prop_pnl_abs)

    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=prop_firm,
        stage_key=stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
    )
    current_personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    if stage_key == "funded" and not hedge_funded:
        current_personal_balance = float(personal_balance_state["Старт личного счета на стадии, $"])

    daily_loss_limit = _stage_daily_loss(prop_firm, stage_key)
    trade = calculate_personal_risk_for_trade(
        config=prop_firm,
        stage_key=stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
        max_risk_per_trade=max_risk,
        target_enabled=True,
        daily_loss_limit=daily_loss_limit,
        hedge_funded=hedge_funded,
    )

    risk_1, risk_2, risk_3, risk_4 = st.columns(4)
    risk_1.metric("Риск пропа", _money(float(trade["Риск пропа, $"])))
    risk_2.metric("Риск личного", _money(float(trade["Риск личного, $"])))
    if float(trade["prop_to_personal_risk_multiple"]) > 0:
        risk_3.metric("Во сколько раз меньше", f"{float(trade['prop_to_personal_risk_multiple']):.2f}x")
    else:
        risk_3.metric("Во сколько раз меньше", "нет хеджа")
    risk_4.metric("Личный риск от пропа", _percent(float(trade["personal_risk_percent_of_prop"])))

    status_1, status_2, status_3, status_4 = st.columns(4)
    status_1.metric("Текущая стадия", stage_options[stage_key])
    status_2.metric("Авто-баланс личного", _money(current_personal_balance))
    status_3.metric("Осталось до цели", _money(float(trade["distance_to_target"])))
    status_4.metric("Осталось до max loss", _money(float(trade["distance_to_max_loss"])))

    st.success(str(trade["target_status"]))
    if consider_news:
        st.info(f"Новости учитываются как forced close, а не как штраф. Текущий сценарий закрытия: {forced_close_r:.2f}R.")

    next_personal_risk = float(trade["Риск личного, $"])
    next_prop_risk = float(trade["Риск пропа, $"])
    deal_table = pd.DataFrame(
        [
            {
                "PnL пропа": _money(current_prop_pnl),
                "Осталось до цели": _money(float(trade["distance_to_target"])),
                "Осталось до max loss": _money(float(trade["distance_to_max_loss"])),
                "Следующий риск пропа": _money(next_prop_risk),
                "Следующий риск личного": _money(next_personal_risk),
                "Баланс личного после Win": _money(current_personal_balance - next_personal_risk),
                "Баланс личного после Loss": _money(current_personal_balance + next_personal_risk),
            }
        ]
    )
    st.dataframe(deal_table, use_container_width=True, hide_index=True)

    payout_profit = current_prop_pnl if stage_key == "funded" else prop_firm.funded.profit_target_for_first_payout
    payout = calculate_funded_payout_preview(
        config=prop_firm,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        funded_profit=payout_profit,
        mode=coverage_mode,
        hedge_funded=hedge_funded,
    )
    payout_1, payout_2, payout_3 = st.columns(3)
    payout_1.metric("Профит на funded", _money(payout["Профит на funded, $"]))
    payout_2.metric("К выплате после сплита", _money(payout["К выплате после сплита, $"]))
    payout_3.metric("Чистыми", _money(payout["Чистыми после личных затрат, $"]))

    if not hedge_funded:
        st.caption("Funded не хеджируется: личный счет фиксируется, а чистыми считается payout минус затраты на challenge и fee.")

    with st.expander("План по стадиям", expanded=False):
        plan_df = pd.DataFrame(
            [
                {
                    "Стадия": row.stage_name,
                    "Старт личного": _money(row.starting_personal_balance),
                    "Риск личного": _money(row.required_personal_risk),
                    "Цель": _money(row.profit_target),
                    "Max loss": _money(row.max_loss),
                    "Баланс после прохода": _money(row.personal_balance_after_stage_passed),
                    "Баланс при потере пропа": _money(row.personal_balance_if_stage_failed),
                }
                for row in stage_plan.rows
            ]
        )
        st.dataframe(plan_df, use_container_width=True, hide_index=True)


def _render_monte_carlo(st, prop_firm: PropFirmConfig, initial_personal_balance: float, prop_risk_percent: float) -> None:
    st.write(
        "Monte Carlo генерирует много случайных Win/Loss-путей по заданному winrate и показывает, как часто модель доходит до выплаты, теряет проп, хватает ли личного счета и какой денежный результат."
    )
    col_1, col_2, col_3, col_4 = st.columns(4)
    strategy_name = col_1.selectbox("Стратегия", ["Зональная", "Непрерывная", "Фиксированная"])
    win_probability = col_2.slider("Winrate", min_value=0.0, max_value=1.0, value=0.55, step=0.01)
    runs = col_3.number_input("Симуляций", min_value=1, value=1000, step=100)
    max_trades = col_4.number_input("Сделок в цикле", min_value=1, value=100, step=10)
    seed = st.number_input("Seed", value=42, step=1)

    if strategy_name == "Фиксированная":
        strategy = FixedPersonalRiskStrategy(risk_amount=st.number_input("Фиксированный риск личного, $", value=20.0, step=5.0))
    elif strategy_name == "Непрерывная":
        strategy = ContinuousPersonalRiskStrategy(
            min_multiplier=st.number_input("Минимум личного риска от пропа", value=0.01, step=0.01),
            max_multiplier=st.number_input("Максимум личного риска от пропа", value=0.08, step=0.01),
            funded_multiplier=st.number_input("Funded: доля риска", value=0.0, step=0.01),
        )
    else:
        strategy = ZonedPersonalRiskStrategy(
            near_loss_multiplier=st.number_input("Около max loss: доля риска", value=0.08, step=0.01),
            mid_multiplier=st.number_input("Середина стадии: доля риска", value=0.04, step=0.01),
            near_target_multiplier=st.number_input("Около цели: доля риска", value=0.01, step=0.01),
            funded_multiplier=st.number_input("Funded: доля риска", value=0.0, step=0.01),
        )

    prop_risk_amount = prop_firm.nominal_balance * prop_risk_percent / 100
    simulation = SimulationConfig(
        prop_firm=replace(prop_firm, prop_risk_per_trade=prop_risk_amount),
        initial_personal_balance=initial_personal_balance,
        win_probability=win_probability,
        max_trades_per_cycle=int(max_trades),
        runs=int(runs),
        seed=int(seed),
    )

    if st.button("Запустить симуляцию", type="primary"):
        result = MonteCarloEngine().run(simulation=simulation, strategy=strategy)
        summary = result.summary
        out_1, out_2, out_3 = st.columns(3)
        out_1.metric("Ожидаемый итог", _money(summary.expected_real_wealth))
        out_2.metric("Вероятность первой выплаты", f"{summary.probability_of_first_payout:.1%}")
        out_3.metric("Провал с покрытием", f"{summary.probability_of_recoverable_failure:.1%}")
        out_4, out_5, out_6 = st.columns(3)
        out_4.metric("Провал без покрытия", f"{summary.probability_of_unrecoverable_failure:.1%}")
        out_5.metric("Среднее внешнее пополнение", _money(summary.average_external_topup))
        out_6.metric("Макс. пополнение до payout", _money(summary.max_external_topup_before_payout))

    with st.expander("Поиск лучшего фиксированного риска", expanded=False):
        amounts_text = st.text_input("Варианты риска личного, $", "0,10,20,40,80")
        if st.button("Найти лучший фиксированный риск"):
            amounts = [float(item.strip()) for item in amounts_text.split(",") if item.strip()]
            optimization = GridSearchOptimizer(engine=MonteCarloEngine()).optimize_fixed_risk(
                simulation=simulation,
                risk_amounts=amounts,
            )
            st.dataframe(
                [
                    {
                        "Риск личного": _money(candidate.risk_amount),
                        "Ожидаемый итог": _money(candidate.summary.expected_real_wealth),
                        "Вероятность payout": f"{candidate.summary.probability_of_first_payout:.1%}",
                    }
                    for candidate in optimization.candidates
                ],
                use_container_width=True,
                hide_index=True,
            )
            st.success(f"Лучший риск: {_money(optimization.best.risk_amount)}")


def _render_research(
    st,
    pd,
    prop_firm: PropFirmConfig,
    stage_options: dict[str, str],
    initial_personal_balance: float,
    prop_risk_percent: float,
    coverage_mode: CoverageMode,
    hedge_funded: bool,
) -> None:
    stage_key = st.selectbox("Стадия для исследования", list(stage_options.keys()), format_func=stage_options.get)
    selected_stage = prop_firm.funded if stage_key == "funded" else prop_firm.stages[int(stage_key.replace("phase_", "")) - 1]
    min_pnl = -selected_stage.max_loss
    max_pnl = selected_stage.profit_target_for_first_payout if stage_key == "funded" else selected_stage.profit_target
    points = st.slider("Количество точек", min_value=5, max_value=101, value=21, step=2)

    rows = []
    for index in range(points):
        pnl = min_pnl + (max_pnl - min_pnl) * index / (points - 1)
        balance_state = calculate_personal_balance_from_prop_pnl(
            prop_firm,
            stage_key,
            pnl,
            initial_personal_balance,
            prop_risk_percent,
            coverage_mode,
        )
        current_personal_balance = float(balance_state["Текущий баланс личного счета, $"])
        trade = calculate_personal_risk_for_trade(
            prop_firm,
            stage_key,
            pnl,
            initial_personal_balance,
            current_personal_balance,
            prop_risk_percent,
            coverage_mode,
            max_risk_per_trade=_stage_max_risk(prop_firm, stage_key),
            daily_loss_limit=_stage_daily_loss(prop_firm, stage_key),
            hedge_funded=hedge_funded,
        )
        rows.append(
            {
                "PnL пропа": _money(pnl),
                "Осталось до цели": _money(float(trade["distance_to_target"])),
                "Осталось до max loss": _money(float(trade["distance_to_max_loss"])),
                "Следующий риск пропа": _money(float(trade["Риск пропа, $"])),
                "Следующий риск личного": _money(float(trade["Риск личного, $"])),
                "Баланс после Win": _money(current_personal_balance - float(trade["Риск личного, $"])),
                "Баланс после Loss": _money(current_personal_balance + float(trade["Риск личного, $"])),
            }
        )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with st.expander("Старый график риска", expanded=False):
        strategy = ZonedPersonalRiskStrategy()
        curve_df = pd.DataFrame(
            build_risk_curve(
                config=prop_firm,
                strategy=strategy,
                stage_key=stage_key,
                personal_balance=initial_personal_balance,
                prop_risk_percent=prop_risk_percent,
                points=81,
            )
        )
        st.line_chart(curve_df.set_index("PnL пропа, $")[["Риск личного счета, $"]])
        st.dataframe(curve_df, use_container_width=True, hide_index=True)

    with st.expander("Инструкция по стадиям", expanded=False):
        instruction = build_dealing_instruction(prop_firm, initial_personal_balance, prop_risk_percent, coverage_mode)
        st.dataframe(pd.DataFrame(instruction), use_container_width=True, hide_index=True)


def _render_instant_accounts(st) -> None:
    col_1, col_2, col_3 = st.columns(3)
    account_size = col_1.number_input(
        "Размер instant-счета, $", value=50_000.0, min_value=1_000.0, step=1_000.0, key="instant_account_size"
    )
    max_loss = col_2.number_input("Max loss, $", value=2_500.0, min_value=1.0, step=100.0, key="instant_max_loss")
    daily_loss = col_3.number_input("Daily loss, $", value=1_250.0, min_value=1.0, step=100.0, key="instant_daily_loss")
    drawdown_mode = st.radio("Drawdown", ["static", "trailing"], horizontal=True, key="instant_drawdown")
    consistency_rule = st.number_input(
        "Consistency rule, %", value=30.0, min_value=0.0, max_value=100.0, step=1.0, key="instant_consistency"
    )
    split = st.number_input("Profit split, %", value=80.0, min_value=1.0, max_value=100.0, step=1.0, key="instant_split")
    target_enabled = st.checkbox("Profit target", value=False, key="instant_target_enabled")
    target = st.number_input(
        "Profit target, $", value=2_000.0, min_value=1.0, step=100.0, disabled=not target_enabled, key="instant_target"
    )
    current_profit = st.number_input("Текущий profit, $", value=0.0, step=100.0, key="instant_current_profit")

    payout = max(0.0, current_profit) * split / 100
    st.metric("К выплате", _money(payout))
    if target_enabled:
        remaining = max(0.0, target - current_profit)
        st.metric("Осталось до цели", _money(remaining))
    st.caption(
        f"Instant = сразу funded. В v1 static drawdown считается как обычный max loss; {drawdown_mode} и consistency {consistency_rule:.0f}% отображаются как ограничения для анализа."
    )


def _render_prop_vs_prop(st, prop_firm: PropFirmConfig) -> None:
    col_1, col_2, col_3 = st.columns(3)
    prop_count = col_1.number_input("Количество пропов", value=2, min_value=2, step=1, key="pvp_prop_count")
    fee_per_prop = col_2.number_input(
        "Fee на один проп, $", value=prop_firm.challenge_fee, min_value=0.0, step=10.0, key="pvp_fee"
    )
    expected_payout = col_3.number_input(
        "Ожидаемый funded profit, $",
        value=prop_firm.funded.profit_target_for_first_payout,
        min_value=0.0,
        step=500.0,
        key="pvp_expected_payout",
    )
    split = st.number_input(
        "Profit split, %", value=prop_firm.funded.trader_split * 100, min_value=1.0, max_value=100.0, step=1.0, key="pvp_split"
    )
    gross_payout = expected_payout * split / 100
    fees = prop_count * fee_per_prop
    net = gross_payout - fees
    st.metric("Потенциально к выплате", _money(gross_payout))
    st.metric("Fees", _money(fees))
    st.metric("После fees", _money(net))
    st.write(
        "Базовая формула режима: funded payout после split минус fees всех купленных пропов. Полная версия должна отдельно учитывать вероятность прохождения каждого пропа."
    )


def _render_prop_selection_principles(st) -> None:
    st.markdown(
        """
- Лучшее соотношение profit target / max loss снижает число нужных Win до прохода.
- Чем ниже target относительно drawdown, тем легче дойти до funded при том же риске.
- Более высокий payout split напрямую повышает net payout.
- Instant быстрее, но обычно дороже и жестче по drawdown; 1 phase проще по пути, 2 phase дольше, но часто дешевле.
- Trailing drawdown хуже static, потому что лимит двигается за максимальным equity.
- Daily loss важен, потому что он может остановить сделку раньше общего max loss.
- Max risk per trade должен быть совместим с остатком до цели: если до цели осталось $300, следующая сделка не должна рисковать $1,900.
"""
    )


def _stage_max_risk(config: PropFirmConfig, stage_key: str) -> float:
    if stage_key == "funded":
        return float(config.funded.max_risk_per_trade or config.prop_risk_per_trade)
    stage = config.stages[int(stage_key.replace("phase_", "")) - 1]
    return float(stage.max_risk_per_trade or config.prop_risk_per_trade)


def _stage_daily_loss(config: PropFirmConfig, stage_key: str) -> float | None:
    if stage_key == "funded":
        return config.funded.daily_loss
    return config.stages[int(stage_key.replace("phase_", "")) - 1].daily_loss


def _money(value: float) -> str:
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2f}%"


def _to_amount(value: float, mode: str, nominal_balance: float) -> float:
    return value if mode == "amount" else nominal_balance * value / 100


def _from_amount(value: float, mode: str, nominal_balance: float) -> float:
    return value if mode == "amount" else value / nominal_balance * 100


if __name__ == "__main__":
    main()
