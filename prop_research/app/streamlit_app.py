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

    settings_summary = st.sidebar.container()
    prop_firm = load_prop_firm_config(Path(config_path))
    prop_firm = _sidebar_rules(st, prop_firm)
    funded_target_enabled = bool(st.session_state.get("funded_profit_target_enabled", True))

    prop_risk_percent = _default_prop_risk_percent(prop_firm)
    recommended_balance = minimum_personal_deposit_for_strict_free_prop(
        config=prop_firm,
        prop_risk_percent=prop_risk_percent,
    ).minimum_personal_deposit
    with settings_summary:
        st.subheader("Риск и личный счет")
        st.metric("Риск пропа по умолчанию", _money(_default_prop_risk_amount(prop_firm)))
        st.caption("Берется из max risk per trade выбранного типа счета.")
        st.metric("Рекомендуемый личный депозит", _money(recommended_balance))
        use_recommended_balance = st.checkbox("Использовать рекомендуемый депозит", value=True)
        if use_recommended_balance:
            initial_personal_balance = float(recommended_balance)
            st.caption(f"Начальный личный депозит: {_money(initial_personal_balance)}")
        else:
            initial_personal_balance = float(
                st.number_input(
                    "Начальный личный депозит, $",
                    value=float(recommended_balance),
                    min_value=0.0,
                    step=10.0,
                    key="initial_personal_balance_custom",
                )
            )
        coverage_label = st.radio(
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
        hedge_funded = not st.checkbox("Не хеджировать выплатной этап", value=False)

    with st.sidebar.expander("Новости", expanded=False):
        consider_news = st.checkbox("Учитывать новости", value=False)
        forced_close_r = st.number_input("Forced close result, R", value=0.0, step=0.1, disabled=not consider_news)

    stage_options = _stage_options(prop_firm)

    calculator_tab, simulation_tab, principles_tab = st.tabs(_enabled_tab_labels())

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
            funded_target_enabled=funded_target_enabled,
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

    default_account_type_index = 2 if _account_type(prop_firm) == "instant" else 1 if len(prop_firm.stages) > 1 else 0
    account_type_label = st.sidebar.selectbox(
        "Тип счета",
        ["1 фаза", "2 фазы", "Инстант"],
        index=default_account_type_index,
        key="account_type_label",
    )
    if account_type_label == "Инстант":
        st.sidebar.subheader("Instant")
        funded = prop_firm.funded
        instant_max_loss_mode = st.sidebar.radio(
            "Instant: max loss режим",
            ["amount", "percent"],
            index=0 if _field(funded, "max_loss_mode", "amount") == "amount" else 1,
            horizontal=True,
            key="instant_max_loss_mode",
        )
        instant_max_loss = st.sidebar.number_input(
            "Instant: max loss",
            value=_from_amount(funded.max_loss, instant_max_loss_mode, prop_firm.nominal_balance),
            min_value=0.01,
            step=500.0 if instant_max_loss_mode == "amount" else 0.1,
            key="instant_max_loss",
        )
        instant_daily_loss_mode = st.sidebar.radio(
            "Instant: daily loss режим",
            ["amount", "percent"],
            index=0 if _field(funded, "daily_loss_mode", "amount") == "amount" else 1,
            horizontal=True,
            key="instant_daily_loss_mode",
        )
        instant_daily_loss = st.sidebar.number_input(
            "Instant: daily loss",
            value=_from_amount(_field(funded, "daily_loss", None) or funded.max_loss / 2, instant_daily_loss_mode, prop_firm.nominal_balance),
            min_value=0.01,
            step=500.0 if instant_daily_loss_mode == "amount" else 0.1,
            key="instant_daily_loss",
        )
        instant_max_risk = st.sidebar.number_input(
            "Instant: max risk per trade, $",
            value=float(_field(funded, "max_risk_per_trade", None) or prop_firm.prop_risk_per_trade),
            min_value=1.0,
            step=100.0,
            key="instant_max_risk",
        )
        instant_drawdown_mode = st.sidebar.radio(
            "Instant: drawdown",
            ["static", "trailing"],
            index=0 if _field(funded, "drawdown_mode", "static") == "static" else 1,
            horizontal=True,
            key="instant_drawdown",
        )
        consistency_enabled = st.sidebar.checkbox("Consistency rule", value=False, key="instant_consistency_enabled")
        st.sidebar.number_input(
            "Consistency rule, %",
            value=30.0,
            min_value=0.0,
            max_value=100.0,
            step=1.0,
            disabled=not consistency_enabled,
            key="instant_consistency",
        )
        instant_split_percent = st.sidebar.number_input(
            "Profit split, %",
            value=funded.trader_split * 100,
            min_value=1.0,
            max_value=100.0,
            step=1.0,
            key="instant_split",
        )
        instant_profit_target_enabled = st.sidebar.checkbox("Profit target", value=True, key="funded_profit_target_enabled")
        instant_profit_target = st.sidebar.number_input(
            "Profit target, $",
            value=funded.profit_target_for_first_payout,
            min_value=1.0,
            step=500.0,
            disabled=not instant_profit_target_enabled,
            key="instant_profit_target",
        )

        return PropFirmConfig(
            challenge_fee=float(challenge_fee),
            nominal_balance=float(nominal_balance),
            stages=[],
            funded=_make_funded_config(
                profit_target_for_first_payout=_funded_target_for_config(
                    enabled=instant_profit_target_enabled,
                    input_value=float(instant_profit_target),
                    existing_value=funded.profit_target_for_first_payout,
                    nominal_balance=float(nominal_balance),
                ),
                max_loss=_to_amount(float(instant_max_loss), instant_max_loss_mode, float(nominal_balance)),
                trader_split=float(instant_split_percent) / 100,
                max_loss_mode=instant_max_loss_mode,
                daily_loss=_to_amount(float(instant_daily_loss), instant_daily_loss_mode, float(nominal_balance)),
                daily_loss_mode=instant_daily_loss_mode,
                max_risk_per_trade=float(instant_max_risk),
                drawdown_mode=instant_drawdown_mode,
            ),
            prop_risk_per_trade=prop_firm.prop_risk_per_trade,
            account_type="instant",
        )

    st.sidebar.subheader("Challenge")
    stages: list[StageConfig] = []
    default_stage_source = prop_firm.stages[0] if prop_firm.stages else StageConfig(name="phase_1", profit_target=6_000.0, max_loss=8_000.0)
    source_stages = prop_firm.stages if len(prop_firm.stages) > 1 else [default_stage_source, default_stage_source]
    for index, default_stage in enumerate(source_stages[: 2 if account_type_label == "2 фазы" else 1], start=1):
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
                index=0 if _field(default_stage, "max_loss_mode", "amount") == "amount" else 1,
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
                index=0 if _field(default_stage, "daily_loss_mode", "amount") == "amount" else 1,
                horizontal=True,
                key=f"phase_{index}_daily_loss_mode",
            )
            default_daily_loss = _field(default_stage, "daily_loss", None) or default_stage.max_loss / 2
            daily_loss_input = st.number_input(
                f"Этап {index}: daily loss",
                value=_from_amount(default_daily_loss, daily_loss_mode, prop_firm.nominal_balance),
                min_value=0.01,
                step=500.0 if daily_loss_mode == "amount" else 0.1,
                key=f"phase_{index}_daily_loss",
            )
            max_risk = st.number_input(
                f"Этап {index}: max risk per trade, $",
                value=float(_field(default_stage, "max_risk_per_trade", None) or prop_firm.prop_risk_per_trade),
                min_value=1.0,
                step=100.0,
                key=f"phase_{index}_max_risk",
            )
            drawdown_mode = st.radio(
                f"Этап {index}: drawdown",
                ["static", "trailing"],
                index=0 if _field(default_stage, "drawdown_mode", "static") == "static" else 1,
                horizontal=True,
                key=f"phase_{index}_drawdown",
            )
            stages.append(
                _make_stage_config(
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
    funded_profit_target_enabled = st.sidebar.checkbox("Funded profit target", value=True, key="funded_profit_target_enabled")
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
        index=0 if _field(funded, "max_loss_mode", "amount") == "amount" else 1,
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
        index=0 if _field(funded, "daily_loss_mode", "amount") == "amount" else 1,
        horizontal=True,
    )
    funded_daily_loss = st.sidebar.number_input(
        "Funded: daily loss",
        value=_from_amount(_field(funded, "daily_loss", None) or funded.max_loss / 2, funded_daily_loss_mode, prop_firm.nominal_balance),
        min_value=0.01,
        step=500.0 if funded_daily_loss_mode == "amount" else 0.1,
    )
    funded_max_risk = st.sidebar.number_input(
        "Funded: max risk per trade, $",
        value=float(_field(funded, "max_risk_per_trade", None) or prop_firm.prop_risk_per_trade),
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
        index=0 if _field(funded, "drawdown_mode", "static") == "static" else 1,
        horizontal=True,
    )
    if funded_drawdown_mode == "trailing":
        st.sidebar.caption("Trailing drawdown требует state machine и будет учитываться в симуляции отдельным шагом.")

    return PropFirmConfig(
        challenge_fee=float(challenge_fee),
        nominal_balance=float(nominal_balance),
        stages=stages,
        funded=_make_funded_config(
            profit_target_for_first_payout=_funded_target_for_config(
                enabled=funded_profit_target_enabled,
                input_value=float(funded_profit_target),
                existing_value=funded.profit_target_for_first_payout,
                nominal_balance=float(nominal_balance),
            ),
            max_loss=_to_amount(float(funded_max_loss), funded_max_loss_mode, float(nominal_balance)),
            trader_split=float(trader_split_percent) / 100,
            max_loss_mode=funded_max_loss_mode,
            daily_loss=_to_amount(float(funded_daily_loss), funded_daily_loss_mode, float(nominal_balance)),
            daily_loss_mode=funded_daily_loss_mode,
            max_risk_per_trade=float(funded_max_risk),
            drawdown_mode=funded_drawdown_mode,
        ),
        prop_risk_per_trade=prop_firm.prop_risk_per_trade,
        account_type="challenge",
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
    funded_target_enabled: bool,
    consider_news: bool,
    forced_close_r: float,
) -> None:
    stage_plan = build_stage_plan(prop_firm, initial_personal_balance, prop_risk_percent, coverage_mode)
    top_1, top_2, top_3 = st.columns(3)
    top_1.metric("Начальный личный депозит", _money(initial_personal_balance))
    top_2.metric("Рекомендуемый личный депозит", _money(recommended_balance))
    top_3.metric("Цена счета" if _account_type(prop_firm) == "instant" else "Цена челленджа", _money(prop_firm.challenge_fee))

    input_1, input_2, input_3 = st.columns(3)
    stage_key = input_1.selectbox("Текущая стадия", list(stage_options.keys()), format_func=stage_options.get)
    max_risk = _stage_max_risk(prop_firm, stage_key)
    trade_risk_key = f"calculator_trade_risk_applied_{stage_key}"
    trade_risk_default_key = f"calculator_trade_risk_default_{stage_key}"
    if (
        trade_risk_key not in st.session_state
        or st.session_state.get(trade_risk_default_key) != max_risk
    ):
        st.session_state[trade_risk_key] = float(max_risk)
        st.session_state[trade_risk_default_key] = float(max_risk)

    with input_2.form(f"calculator_trade_risk_form_{stage_key}", border=False):
        entered_trade_risk = st.number_input(
            "Риск пропа в сделке, $",
            value=float(st.session_state[trade_risk_key]),
            min_value=1.0,
            step=100.0,
        )
        trade_risk_submitted = st.form_submit_button("Применить", use_container_width=True)
    if trade_risk_submitted:
        st.session_state[trade_risk_key] = float(entered_trade_risk)
    current_trade_prop_risk = float(st.session_state[trade_risk_key])
    input_2.caption(f"Сейчас применяется: {_money(current_trade_prop_risk)}")
    stage_prop_risk_percent = _risk_percent_from_amount(current_trade_prop_risk, prop_firm.nominal_balance)

    if "calculator_current_prop_pnl" not in st.session_state:
        st.session_state["calculator_current_prop_pnl"] = 0.0
    if "calculator_is_drawdown" not in st.session_state:
        st.session_state["calculator_is_drawdown"] = False

    def sync_pnl_sign() -> None:
        raw_value = float(st.session_state.get("calculator_current_prop_pnl", 0.0))
        if st.session_state.get("calculator_is_drawdown", False):
            st.session_state["calculator_current_prop_pnl"] = -abs(raw_value)
        else:
            st.session_state["calculator_current_prop_pnl"] = abs(raw_value)

    current_prop_pnl_raw = input_3.number_input(
        "Текущий PnL пропа, $",
        step=float(current_trade_prop_risk),
        key="calculator_current_prop_pnl",
        on_change=sync_pnl_sign,
    )
    is_drawdown = input_3.checkbox("Это просадка", key="calculator_is_drawdown", on_change=sync_pnl_sign)
    current_prop_pnl = float(st.session_state.get("calculator_current_prop_pnl", current_prop_pnl_raw))
    target_enabled_for_stage = stage_key != "funded" or funded_target_enabled

    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=prop_firm,
        stage_key=stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=stage_prop_risk_percent,
        mode=coverage_mode,
    )
    stage_starting_personal_balance = float(personal_balance_state["Старт личного счета на стадии, $"])
    current_personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    if stage_key == "funded" and not hedge_funded:
        current_personal_balance = stage_starting_personal_balance
    personal_spent = _personal_spent(stage_starting_personal_balance, current_personal_balance)

    daily_loss_limit = _stage_daily_loss(prop_firm, stage_key)
    trade = calculate_personal_risk_for_trade(
        config=prop_firm,
        stage_key=stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        prop_risk_percent=stage_prop_risk_percent,
        mode=coverage_mode,
        max_risk_per_trade=current_trade_prop_risk,
        target_enabled=target_enabled_for_stage,
        daily_loss_limit=daily_loss_limit,
        hedge_funded=hedge_funded,
    )

    risk_1, risk_2, risk_3 = st.columns(3)
    risk_1.metric("Риск пропа", _money(float(trade["Риск пропа, $"])))
    risk_2.metric("Риск личного", _money(float(trade["Риск личного, $"])))
    risk_2.caption(
        _hedge_summary_display(
            multiplier=float(trade["prop_to_personal_risk_multiple"]),
            personal_percent=float(trade["personal_risk_percent_of_prop"]),
        )
    )
    risk_3.metric("Потрачено личных", _money(personal_spent))

    status_1, status_2, status_3 = st.columns(3)
    status_1.metric("Баланс личного", _money(current_personal_balance))
    status_2.metric("Осталось до цели", _target_distance_display(target_enabled_for_stage, float(trade["distance_to_target"])))
    status_3.metric("Осталось до max loss", _money(float(trade["distance_to_max_loss"])))

    if target_enabled_for_stage:
        st.success(str(trade["target_status"]))
    if consider_news:
        st.info(f"Новости учитываются как forced close, а не как штраф. Текущий сценарий закрытия: {forced_close_r:.2f}R.")

    next_personal_risk = float(trade["Риск личного, $"])
    next_prop_risk = float(trade["Риск пропа, $"])
    deal_table = pd.DataFrame(
        [
            {
                "PnL пропа": _money(current_prop_pnl),
                "Потрачено личных": _money(personal_spent),
                "Осталось до цели": _target_distance_display(target_enabled_for_stage, float(trade["distance_to_target"])),
                "Осталось до max loss": _money(float(trade["distance_to_max_loss"])),
                "Следующий риск пропа": _money(next_prop_risk),
                "Следующий риск личного": _money(next_personal_risk),
                "Баланс личного после Win": _money(current_personal_balance - next_personal_risk),
                "Баланс личного после Loss": _money(current_personal_balance + next_personal_risk),
            }
        ]
    )
    st.dataframe(deal_table, use_container_width=True, hide_index=True)

    if stage_key == "funded" or funded_target_enabled:
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
        payout_profit_label = "Профит на instant" if _account_type(prop_firm) == "instant" else "Профит на funded"
        payout_1.metric(payout_profit_label, _money(payout["Профит на funded, $"]))
        payout_2.metric("К выплате после сплита", _money(payout["К выплате после сплита, $"]))
        payout_3.metric("Чистыми", _money(payout["Чистыми после личных затрат, $"]))

        if not hedge_funded:
            st.caption("Выплатной этап не хеджируется: личный счет фиксируется, а чистыми считается payout минус затраты на счет.")

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
    funded_target_enabled: bool,
) -> None:
    stage_key = st.selectbox("Стадия для исследования", list(stage_options.keys()), format_func=stage_options.get)
    selected_stage = prop_firm.funded if stage_key == "funded" else prop_firm.stages[int(stage_key.replace("phase_", "")) - 1]
    min_pnl = -selected_stage.max_loss
    max_pnl = selected_stage.profit_target_for_first_payout if stage_key == "funded" else selected_stage.profit_target
    target_enabled_for_stage = stage_key != "funded" or funded_target_enabled
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
            target_enabled=target_enabled_for_stage,
            daily_loss_limit=_stage_daily_loss(prop_firm, stage_key),
            hedge_funded=hedge_funded,
        )
        rows.append(
            {
                "PnL пропа": _money(pnl),
                "Осталось до цели": _target_distance_display(target_enabled_for_stage, float(trade["distance_to_target"])),
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
        return float(_field(config.funded, "max_risk_per_trade", None) or config.prop_risk_per_trade)
    stage = config.stages[int(stage_key.replace("phase_", "")) - 1]
    return float(_field(stage, "max_risk_per_trade", None) or config.prop_risk_per_trade)


def _default_prop_risk_amount(config: PropFirmConfig) -> float:
    if _account_type(config) == "instant":
        return _stage_max_risk(config, "funded")
    return _stage_max_risk(config, "phase_1")


def _stage_risk_percent(config: PropFirmConfig, stage_key: str) -> float:
    return _risk_percent_from_amount(_stage_max_risk(config, stage_key), config.nominal_balance)


def _risk_percent_from_amount(risk_amount: float, nominal_balance: float) -> float:
    if nominal_balance <= 0:
        return 0.0
    return round(risk_amount / nominal_balance * 100, 6)


def _personal_spent(starting_balance: float, current_balance: float) -> float:
    return round(max(0.0, starting_balance - current_balance), 2)


def _hedge_summary_display(multiplier: float, personal_percent: float) -> str:
    if multiplier <= 0:
        return "нет хеджа"
    compact_multiplier = f"{multiplier:.0f}x" if multiplier.is_integer() else f"{multiplier:.1f}x"
    return f"{personal_percent:.2f}% от пропа · {compact_multiplier} меньше"


def _default_prop_risk_percent(config: PropFirmConfig) -> float:
    if _account_type(config) == "instant":
        return _stage_risk_percent(config, "funded")
    return _stage_risk_percent(config, "phase_1")


def _stage_options(config: PropFirmConfig) -> dict[str, str]:
    if _account_type(config) == "instant":
        return {"funded": "Instant счет"}
    return {
        **{f"phase_{index + 1}": f"Этап {index + 1}: {stage.name}" for index, stage in enumerate(config.stages)},
        "funded": "Funded до первой выплаты",
    }


def _account_type(config) -> str:
    return getattr(config, "account_type", "challenge")


def _enabled_tab_labels() -> list[str]:
    return [
        "Калькулятор сделки",
        "Симуляции / Monte Carlo",
        "Принципы выбора пропа",
    ]


def _stage_daily_loss(config: PropFirmConfig, stage_key: str) -> float | None:
    if stage_key == "funded":
        return _field(config.funded, "daily_loss", None)
    return _field(config.stages[int(stage_key.replace("phase_", "")) - 1], "daily_loss", None)


def _field(obj, name: str, default):
    return getattr(obj, name, default)


def _funded_target_for_config(
    enabled: bool,
    input_value: float,
    existing_value: float,
    nominal_balance: float,
) -> float:
    if enabled:
        return float(input_value)
    return float(existing_value or nominal_balance)


def _target_distance_display(enabled: bool, distance: float) -> str:
    if not enabled:
        return ""
    return _money(distance)


def _make_stage_config(**kwargs) -> StageConfig:
    try:
        return StageConfig(**kwargs)
    except TypeError:
        return StageConfig(
            name=kwargs["name"],
            profit_target=kwargs["profit_target"],
            max_loss=kwargs["max_loss"],
        )


def _make_funded_config(**kwargs) -> FundedConfig:
    try:
        return FundedConfig(**kwargs)
    except TypeError:
        return FundedConfig(
            profit_target_for_first_payout=kwargs["profit_target_for_first_payout"],
            max_loss=kwargs["max_loss"],
            trader_split=kwargs["trader_split"],
        )


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
