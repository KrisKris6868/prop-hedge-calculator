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
    calculate_personal_balance_from_prop_pnl,
    calculate_personal_risk_for_trade,
    minimum_personal_deposit_for_strict_free_prop,
)
from prop_research.app.risk_curve import build_risk_curve
from prop_research.config.loader import load_prop_firm_config
from prop_research.optimization.grid_search import GridSearchOptimizer
from prop_research.simulation.monte_carlo import MonteCarloEngine, SimulationConfig
from prop_research.strategies.continuous import ContinuousPersonalRiskStrategy
from prop_research.strategies.fixed import FixedPersonalRiskStrategy
from prop_research.strategies.zoned import ZonedPersonalRiskStrategy


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Prop Research", layout="wide")
    st.title("Расчет личного риска для 1к1 хеджа")
    st.caption(
        "Проп-счет и личный счет открывают один и тот же инструмент в противоположные стороны. "
        "Stop Loss и Take Profit считаются 1 к 1. Рынок не моделируется: есть только Win/Loss."
    )

    config_path = st.sidebar.text_input("Файл правил проп-фирмы", "configs/example_prop_firm.json")
    prop_firm = load_prop_firm_config(Path(config_path))

    st.sidebar.subheader("Цена проп-счета")
    challenge_fee = st.sidebar.number_input("Цена челленджа, $", value=prop_firm.challenge_fee, min_value=1.0, step=10.0)
    nominal_balance = st.sidebar.number_input(
        "Размер проп-счета, $",
        value=prop_firm.nominal_balance,
        min_value=1_000.0,
        step=1_000.0,
    )
    prop_firm = replace(
        prop_firm,
        challenge_fee=float(challenge_fee),
        nominal_balance=float(nominal_balance),
    )

    initial_personal_balance = st.sidebar.number_input("Баланс личного счета, $", value=200.0, step=10.0)
    prop_risk_percent = st.sidebar.number_input("Риск проп-счета на сделку, %", value=1.0, step=0.1)
    prop_risk_amount_for_step = prop_firm.nominal_balance * prop_risk_percent / 100
    st.sidebar.subheader("Funded")
    payout_profit_target = st.sidebar.number_input(
        "Профит до первой выплаты, $",
        value=prop_firm.funded.profit_target_for_first_payout,
        min_value=1.0,
        step=500.0,
    )
    trader_split_percent = st.sidebar.number_input(
        "Профит сплит трейдера, %",
        value=prop_firm.funded.trader_split * 100,
        min_value=1.0,
        max_value=100.0,
        step=1.0,
    )
    prop_firm = replace(
        prop_firm,
        funded=replace(
            prop_firm.funded,
            profit_target_for_first_payout=float(payout_profit_target),
            trader_split=float(trader_split_percent) / 100,
        ),
    )
    coverage_label = st.sidebar.radio(
        "Что должно произойти при потере пропа",
        [
            "Личный депозит должен вырасти на цену челленджа",
            "На личном счете должно хватить на новый челлендж",
        ],
    )
    coverage_mode = (
        CoverageMode.GROW_DEPOSIT_BY_FEE
        if coverage_label == "Личный депозит должен вырасти на цену челленджа"
        else CoverageMode.BALANCE_COVERS_NEXT_CHALLENGE
    )
    stage_options = {
        **{f"phase_{index + 1}": f"Этап {index + 1}: {stage.name}" for index, stage in enumerate(prop_firm.stages)},
        "funded": "Funded до первой выплаты",
    }

    st.subheader("0. Калькулятор одной сделки")
    st.write(
        "Введи текущее состояние, и калькулятор даст один ответ: сколько долларов риска поставить "
        "на личном счете. Сделка всегда зеркальная 1к1 по направлению."
    )
    calc_col_1, calc_col_2 = st.columns(2)
    calculator_stage_key = calc_col_1.selectbox(
        "Текущая стадия",
        list(stage_options.keys()),
        format_func=stage_options.get,
        key="calculator_stage",
    )
    current_prop_pnl_abs = calc_col_2.number_input(
        "Текущий PnL пропа на стадии, $",
        value=0.0,
        step=float(prop_risk_amount_for_step) if prop_risk_amount_for_step > 0 else 100.0,
        help="Кнопки +/- двигают PnL на размер риска пропа. Вручную можно ввести любое число.",
    )
    is_drawdown = calc_col_2.checkbox("Это просадка", value=False)
    current_prop_pnl = -abs(current_prop_pnl_abs) if is_drawdown else abs(current_prop_pnl_abs)
    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=prop_firm,
        stage_key=calculator_stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
    )
    current_personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    trade_instruction = calculate_personal_risk_for_trade(
        config=prop_firm,
        stage_key=calculator_stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
    )
    action_col_1, action_col_2, action_col_3 = st.columns(3)
    action_col_1.metric("Риск пропа", f"${float(trade_instruction['Риск пропа, $']):,.2f}")
    action_col_2.metric("Открыть риск на личном", f"${float(trade_instruction['Риск личного, $']):,.2f}")
    action_col_3.metric("Loss до потери пропа", f"{float(trade_instruction['Loss до потери пропа']):.2f}")
    balance_col_1, balance_col_2, balance_col_3 = st.columns(3)
    balance_col_1.metric(
        "Старт личного на стадии",
        f"${float(personal_balance_state['Старт личного счета на стадии, $']):,.2f}",
    )
    balance_col_2.metric(
        "Авто-баланс личного сейчас",
        f"${current_personal_balance:,.2f}",
    )
    balance_col_3.metric(
        "Изменение личного на стадии",
        f"${float(personal_balance_state['Изменение личного счета на стадии, $']):,.2f}",
    )
    st.info(
        f"Если на пропе Long, на личном Short. Если на пропе Short, на личном Long. "
        f"Цель личного счета при потере пропа: "
        f"${float(trade_instruction['Цель личного счета при потере пропа, $']):,.2f}."
    )

    stage_plan = build_stage_plan(
        config=prop_firm,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
    )
    dealing_instruction = build_dealing_instruction(
        config=prop_firm,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=coverage_mode,
    )
    free_prop_requirement = minimum_personal_deposit_for_strict_free_prop(
        config=prop_firm,
        prop_risk_percent=prop_risk_percent,
    )

    st.subheader("1. Можно ли сделать проп бесплатным?")
    st.write(
        "Если понимать «бесплатно» строго: при потере пропа личный счет должен восстановить цену "
        "нового челленджа, а при успешном пути до первой выплаты не должно потребоваться дополнительных денег."
    )

    col_free_1, col_free_2, col_free_3 = st.columns(3)
    col_free_1.metric("Минимальная личная подушка", f"${free_prop_requirement.minimum_personal_deposit:,.2f}")
    col_free_2.metric("Цена челленджа", f"${free_prop_requirement.challenge_fee:,.2f}")
    col_free_3.metric("Капитал до первой выплаты", f"${free_prop_requirement.total_capital_before_payout:,.2f}")

    if initial_personal_balance >= free_prop_requirement.minimum_personal_deposit:
        st.success(
            "С такой личной подушкой строгая модель теоретически помещается до первой выплаты."
        )
    else:
        missing = free_prop_requirement.minimum_personal_deposit - initial_personal_balance
        st.error(
            f"С личным депозитом ${initial_personal_balance:,.2f} строгий бесплатный режим не сходится. "
            f"Не хватает примерно ${missing:,.2f} личной подушки. "
            "Это не вопрос психологии или интерфейса, а следствие правил: проп должен пройти несколько целей, "
            "а зеркальный личный счет в это время теряет деньги."
        )

    st.subheader("2. Автоматический расчет риска по этапам")
    st.write(
        "Это расчет не по психологии и не по догадке. Для каждой стадии программа считает, "
        "какой риск на личном счете нужен, чтобы при вылете пропа по max loss выполнить выбранное условие."
    )

    col_auto_1, col_auto_2, col_auto_3 = st.columns(3)
    col_auto_1.metric("Риск пропа на сделку", f"${stage_plan.prop_risk_amount:,.0f}")
    col_auto_2.metric("Цена челленджа", f"${prop_firm.challenge_fee:,.0f}")
    col_auto_3.metric("Личный депозит", f"${initial_personal_balance:,.0f}")

    plan_df = pd.DataFrame(
        [
            {
                "Стадия": row.stage_name,
                "Старт личного счета, $": row.starting_personal_balance,
                "Нужный риск личного, $": row.required_personal_risk,
                "Нужный риск личного, % от риска пропа": round(
                    row.required_personal_risk / stage_plan.prop_risk_amount * 100, 2
                )
                if stage_plan.prop_risk_amount > 0
                else 0.0,
                "Если стадия пройдена: потеря личного, $": row.personal_loss_if_stage_passed,
                "Личный счет после прохода, $": row.personal_balance_after_stage_passed,
                "Личный счет при вылете пропа, $": row.personal_balance_if_stage_failed,
            }
            for row in stage_plan.rows
        ]
    )
    st.dataframe(plan_df, use_container_width=True, hide_index=True)

    if stage_plan.feasible_with_initial_deposit:
        st.success(
            "Эта модель помещается в текущий личный депозит на пути до первой выплаты. "
            f"Если все стадии будут пройдены, расчетный остаток личного счета: "
            f"${stage_plan.personal_balance_at_first_payout_path:,.2f}."
        )
    else:
        st.error(
            "Строгая модель не помещается в текущий личный депозит. "
            f"Если проп пройдет все стадии до первой выплаты, личный счет уйдет до "
            f"${stage_plan.personal_balance_at_first_payout_path:,.2f}. "
            "Это означает, что одновременно гарантировать рост депозита на цену челленджа "
            "при любом вылете и не превысить депозит при успешном пути нельзя с этими параметрами."
        )

    st.write(
        "Ключевая формула: если проп от текущей точки до max loss должен потерять `N` стопов, "
        "то личный риск на сделку равен требуемому покрытию, деленному на `N`."
    )

    st.subheader("3. Инструкция для открытия сделки")
    st.write(
        "Эту таблицу можно читать практически: нашла текущую стадию, смотришь риск пропа и ставишь "
        "соответствующий риск на личном счете. Направление всегда противоположное: если на пропе Long, "
        "на личном Short; если на пропе Short, на личном Long."
    )
    instruction_df = pd.DataFrame(dealing_instruction)
    st.dataframe(instruction_df, use_container_width=True, hide_index=True)

    gross_payout = prop_firm.funded.profit_target_for_first_payout
    payout_after_split = gross_payout * prop_firm.funded.trader_split
    net_after_personal_costs = payout_after_split - stage_plan.personal_loss_if_all_targets_hit
    st.subheader("4. Расчет выплаты на funded")
    payout_col_1, payout_col_2, payout_col_3 = st.columns(3)
    payout_col_1.metric("Профит до выплаты", f"${gross_payout:,.2f}")
    payout_col_2.metric("К выплате после сплита", f"${payout_after_split:,.2f}")
    payout_col_3.metric("Чистыми после личных затрат", f"${net_after_personal_costs:,.2f}")
    st.write(
        f"Формула: `${gross_payout:,.2f} * {prop_firm.funded.trader_split:.0%} = "
        f"${payout_after_split:,.2f}`. Затем вычитаем расчетные затраты личного счета "
        f"до выплаты: `${stage_plan.personal_loss_if_all_targets_hit:,.2f}`."
    )

    strategy_name = st.sidebar.selectbox(
        "Дополнительная исследовательская модель",
        ["Зональная", "Непрерывная", "Фиксированная"],
    )
    win_probability = st.sidebar.slider("Вероятность Win", min_value=0.0, max_value=1.0, value=0.55, step=0.01)
    runs = st.sidebar.number_input("Количество симуляций", min_value=1, value=1000, step=100)
    max_trades = st.sidebar.number_input("Максимум сделок в цикле", min_value=1, value=100, step=10)
    seed = st.sidebar.number_input("Seed", value=42, step=1)

    if strategy_name == "Фиксированная":
        fixed_risk = st.sidebar.number_input("Фиксированный риск личного счета, $", value=20.0, step=5.0)
        strategy = FixedPersonalRiskStrategy(risk_amount=fixed_risk)
    elif strategy_name == "Непрерывная":
        strategy = ContinuousPersonalRiskStrategy(
            min_multiplier=st.sidebar.number_input("Минимум личного риска от риска пропа", value=0.01, step=0.01),
            max_multiplier=st.sidebar.number_input("Максимум личного риска от риска пропа", value=0.08, step=0.01),
            funded_multiplier=st.sidebar.number_input("Риск на funded от риска пропа", value=0.0, step=0.01),
        )
    else:
        strategy = ZonedPersonalRiskStrategy(
            near_loss_multiplier=st.sidebar.number_input("Около max loss: доля от риска пропа", value=0.08, step=0.01),
            mid_multiplier=st.sidebar.number_input("Середина этапа: доля от риска пропа", value=0.04, step=0.01),
            near_target_multiplier=st.sidebar.number_input("Около цели: доля от риска пропа", value=0.01, step=0.01),
            funded_multiplier=st.sidebar.number_input("Funded: доля от риска пропа", value=0.0, step=0.01),
        )

    selected_stage_key = st.selectbox("Стадия проп-счета", list(stage_options.keys()), format_func=stage_options.get)
    curve_rows = build_risk_curve(
        config=prop_firm,
        strategy=strategy,
        stage_key=selected_stage_key,
        personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        points=81,
    )
    curve_df = pd.DataFrame(curve_rows)
    prop_risk_amount = prop_firm.nominal_balance * prop_risk_percent / 100

    st.subheader("5. График риска внутри выбранной стадии")
    st.write(
        "Этот график нужен для ориентира внутри стадии: где проп сейчас находится относительно цели и max loss. "
        "Он показывает риск выбранной исследовательской модели, а таблица выше показывает автоматический "
        "страховой расчет по этапам."
    )

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("Номинал проп-счета", f"${prop_firm.nominal_balance:,.0f}")
    col_b.metric("Риск пропа на сделку", f"${prop_risk_amount:,.0f}")
    col_c.metric("Цена нового челленджа", f"${prop_firm.challenge_fee:,.0f}")

    chart_df = curve_df.set_index("PnL пропа, $")[["Риск личного счета, $"]]
    st.line_chart(chart_df)

    min_pnl = float(curve_df["PnL пропа, $"].min())
    max_pnl = float(curve_df["PnL пропа, $"].max())
    current_pnl = st.slider("Текущий PnL пропа на этой стадии, $", min_pnl, max_pnl, 0.0, step=100.0)
    nearest_index = (curve_df["PnL пропа, $"] - current_pnl).abs().idxmin()
    current_row = curve_df.loc[nearest_index]
    current_personal_risk = float(current_row["Риск личного счета, $"])
    stops_to_cover_fee = prop_firm.challenge_fee / current_personal_risk if current_personal_risk > 0 else float("inf")

    col_d, col_e, col_f = st.columns(3)
    col_d.metric("Рекомендованный риск личного счета", f"${current_personal_risk:,.2f}")
    col_e.metric("Это от риска пропа", f"{float(current_row['Риск личного / риск пропа, %']):.2f}%")
    col_f.metric(
        "Стопов пропа до покрытия челленджа",
        "нет хеджа" if stops_to_cover_fee == float("inf") else f"{stops_to_cover_fee:.1f}",
    )

    st.dataframe(curve_df, use_container_width=True, hide_index=True)

    simulation = SimulationConfig(
        prop_firm=prop_firm.__class__(
            challenge_fee=prop_firm.challenge_fee,
            nominal_balance=prop_firm.nominal_balance,
            stages=prop_firm.stages,
            funded=prop_firm.funded,
            prop_risk_per_trade=prop_risk_amount,
        ),
        initial_personal_balance=initial_personal_balance,
        win_probability=win_probability,
        max_trades_per_cycle=int(max_trades),
        runs=int(runs),
        seed=int(seed),
    )

    st.subheader("6. Monte Carlo-проверка стратегии")
    if st.button("Запустить симуляцию", type="primary"):
        result = MonteCarloEngine().run(simulation=simulation, strategy=strategy)
        summary = result.summary

        col1, col2, col3 = st.columns(3)
        col1.metric("Ожидаемое итоговое богатство", f"${summary.expected_real_wealth:,.2f}")
        col2.metric("Вероятность первой выплаты", f"{summary.probability_of_first_payout:.1%}")
        col3.metric("Провал с покрытием нового челленджа", f"{summary.probability_of_recoverable_failure:.1%}")

        col4, col5, col6 = st.columns(3)
        col4.metric("Провал без покрытия челленджа", f"{summary.probability_of_unrecoverable_failure:.1%}")
        col5.metric("Среднее внешнее пополнение", f"${summary.average_external_topup:,.2f}")
        col6.metric("Максимальное внешнее пополнение", f"${summary.max_external_topup_before_payout:,.2f}")

    with st.expander("Поиск лучшего фиксированного риска"):
        amounts_text = st.text_input("Варианты риска личного счета, $", "0,10,20,40,80")
        if st.button("Найти лучший фиксированный риск"):
            amounts = [float(item.strip()) for item in amounts_text.split(",") if item.strip()]
            optimization = GridSearchOptimizer(engine=MonteCarloEngine()).optimize_fixed_risk(
                simulation=simulation,
                risk_amounts=amounts,
            )
            st.write(
                [
                    {
                        "Риск личного счета, $": candidate.risk_amount,
                        "Ожидаемое итоговое богатство": candidate.summary.expected_real_wealth,
                        "Вероятность первой выплаты": candidate.summary.probability_of_first_payout,
                        "Провал с покрытием челленджа": candidate.summary.probability_of_recoverable_failure,
                    }
                    for candidate in optimization.candidates
                ]
            )
            st.success(f"Лучший фиксированный риск: ${optimization.best.risk_amount:,.2f}")


if __name__ == "__main__":
    main()
