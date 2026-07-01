from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path
import subprocess
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _app_version() -> str:
    for env_name in ("GITHUB_SHA", "SOURCE_VERSION", "STREAMLIT_GIT_COMMIT"):
        value = os.environ.get(env_name)
        if value:
            return value[:7]
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "online"

from prop_research.app.hedge_model import (
    CoverageMode,
    TrailingRiskMode,
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
    st.sidebar.caption(f"Версия: {_app_version()}")

    with st.sidebar.expander("Расширенные настройки", expanded=False):
        config_path = st.text_input("Файл правил", "configs/example_prop_firm.json")

    settings_summary = st.sidebar.container()
    prop_firm = load_prop_firm_config(Path(config_path))
    prop_firm = _sidebar_rules(st, prop_firm)
    funded_target_enabled = bool(st.session_state.get("funded_profit_target_enabled", True))
    hedge_funded = not bool(st.session_state.get("skip_funded_hedge", False))
    trailing_risk_mode = _sidebar_trailing_risk_mode(st, prop_firm)

    prop_risk_percent = _default_prop_risk_percent(prop_firm)
    recommended_balance = minimum_personal_deposit_for_strict_free_prop(
        config=prop_firm,
        prop_risk_percent=prop_risk_percent,
        hedge_funded=hedge_funded,
        trailing_risk_mode=trailing_risk_mode,
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
        hedge_funded = not st.checkbox("Не хеджировать выплатной этап", value=not hedge_funded, key="skip_funded_hedge")
        st.checkbox("Проверять маржу личного hedge", value=False, key="hedge_margin_check_enabled")

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
            trailing_risk_mode=trailing_risk_mode,
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
            key="account_instant_max_loss_mode",
        )
        instant_max_loss = st.sidebar.number_input(
            "Instant: max loss",
            value=_from_amount(funded.max_loss, instant_max_loss_mode, prop_firm.nominal_balance),
            min_value=0.01,
            step=500.0 if instant_max_loss_mode == "amount" else 0.1,
            key="account_instant_max_loss",
        )
        instant_daily_loss_mode = st.sidebar.radio(
            "Instant: daily loss режим",
            ["amount", "percent"],
            index=0 if _field(funded, "daily_loss_mode", "amount") == "amount" else 1,
            horizontal=True,
            key="account_instant_daily_loss_mode",
        )
        instant_daily_loss = st.sidebar.number_input(
            "Instant: daily loss",
            value=_from_amount(_field(funded, "daily_loss", None) or funded.max_loss / 2, instant_daily_loss_mode, prop_firm.nominal_balance),
            min_value=0.01,
            step=500.0 if instant_daily_loss_mode == "amount" else 0.1,
            key="account_instant_daily_loss",
        )
        instant_max_risk = st.sidebar.number_input(
            "Instant: max risk per trade, $",
            value=float(_field(funded, "max_risk_per_trade", None) or prop_firm.prop_risk_per_trade),
            min_value=1.0,
            step=100.0,
            key="account_instant_max_risk",
        )
        instant_drawdown_mode = st.sidebar.radio(
            "Instant: drawdown",
            ["static", "trailing"],
            index=0 if _field(funded, "drawdown_mode", "static") == "static" else 1,
            horizontal=True,
            key="account_instant_drawdown",
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
        minimum_days_enabled = st.sidebar.checkbox("Minimum profitable days", value=False, key="minimum_profitable_days_enabled")
        st.sidebar.number_input(
            "Minimum profitable days",
            value=5,
            min_value=0,
            step=1,
            disabled=not minimum_days_enabled,
            key="minimum_profitable_days_required",
        )
        st.sidebar.number_input(
            "Minimum day profit, % от счета",
            value=0.5,
            min_value=0.0,
            step=0.1,
            disabled=not minimum_days_enabled,
            key="minimum_profitable_day_percent",
        )
        instant_split_percent = st.sidebar.number_input(
            "Profit split, %",
            value=funded.trader_split * 100,
            min_value=1.0,
            max_value=100.0,
            step=1.0,
            key="account_instant_split",
        )
        instant_profit_target_enabled = st.sidebar.checkbox("Profit target", value=True, key="funded_profit_target_enabled")
        instant_profit_target = st.sidebar.number_input(
            "Profit target, $",
            value=funded.profit_target_for_first_payout,
            min_value=1.0,
            step=500.0,
            disabled=not instant_profit_target_enabled,
            key="account_instant_profit_target",
        )
        instant_max_loss_amount = _positive_amount(
            _to_amount(float(instant_max_loss), instant_max_loss_mode, float(nominal_balance)),
            fallback=funded.max_loss,
        )
        instant_daily_loss_amount = _positive_amount(
            _to_amount(float(instant_daily_loss), instant_daily_loss_mode, float(nominal_balance)),
            fallback=_field(funded, "daily_loss", None) or funded.max_loss / 2,
        )

        return _make_prop_firm_config(
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
                max_loss=instant_max_loss_amount,
                trader_split=float(instant_split_percent) / 100,
                max_loss_mode=instant_max_loss_mode,
                daily_loss=instant_daily_loss_amount,
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
            phase_consistency_enabled = st.checkbox("Consistency rule", value=False, key=f"phase_{index}_consistency_enabled")
            st.number_input(
                "Consistency rule, %",
                value=15.0,
                min_value=0.0,
                max_value=100.0,
                step=1.0,
                disabled=not phase_consistency_enabled,
                key=f"phase_{index}_consistency",
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
    funded_consistency_enabled = st.sidebar.checkbox("Funded: consistency rule", value=False, key="funded_consistency_enabled")
    st.sidebar.number_input(
        "Funded: consistency rule, %",
        value=15.0,
        min_value=0.0,
        max_value=100.0,
        step=1.0,
        disabled=not funded_consistency_enabled,
        key="funded_consistency",
    )
    minimum_days_enabled = st.sidebar.checkbox("Funded: minimum profitable days", value=False, key="minimum_profitable_days_enabled")
    st.sidebar.number_input(
        "Funded: minimum profitable days",
        value=5,
        min_value=0,
        step=1,
        disabled=not minimum_days_enabled,
        key="minimum_profitable_days_required",
    )
    st.sidebar.number_input(
        "Funded: minimum day profit, % от счета",
        value=0.5,
        min_value=0.0,
        step=0.1,
        disabled=not minimum_days_enabled,
        key="minimum_profitable_day_percent",
    )
    if funded_drawdown_mode == "trailing":
        st.sidebar.caption("Trailing drawdown требует state machine и будет учитываться в симуляции отдельным шагом.")

    return _make_prop_firm_config(
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


def _sidebar_trailing_risk_mode(st, prop_firm: PropFirmConfig) -> TrailingRiskMode:
    if not _has_trailing_drawdown(prop_firm):
        return TrailingRiskMode.CONSERVATIVE
    labels = {
        TrailingRiskMode.ADAPTIVE: "Адаптивная",
        TrailingRiskMode.CONSERVATIVE: "Консервативная",
    }
    default_mode = TrailingRiskMode.ADAPTIVE
    mode_label = st.sidebar.selectbox(
        "Trailing расчет риска",
        list(labels.values()),
        index=list(labels).index(default_mode),
        key="trailing_risk_mode_label_v2",
        help="Адаптивная модель считает от текущего high watermark. Консервативная резервирует откат после достижения цели.",
    )
    for mode, label in labels.items():
        if label == mode_label:
            return mode
    return default_mode


def _render_trade_calculator(
    st,
    pd,
    prop_firm: PropFirmConfig,
    stage_options: dict[str, str],
    initial_personal_balance: float,
    recommended_balance: float,
    prop_risk_percent: float,
    coverage_mode: CoverageMode,
    trailing_risk_mode: TrailingRiskMode,
    hedge_funded: bool,
    funded_target_enabled: bool,
    consider_news: bool,
    forced_close_r: float,
) -> None:
    stage_plan = build_stage_plan(
        prop_firm,
        initial_personal_balance,
        prop_risk_percent,
        coverage_mode,
        trailing_risk_mode=trailing_risk_mode,
    )
    account_type = _account_type(prop_firm)
    top_1, top_2, top_3 = st.columns(3)
    top_1.metric("Начальный личный депозит", _money(initial_personal_balance))
    top_2.metric("Рекомендуемый личный депозит", _money(recommended_balance))
    top_3.metric("Цена счета" if account_type == "instant" else "Цена челленджа", _money(prop_firm.challenge_fee))

    input_1, input_2, input_3 = st.columns(3)
    stage_keys = list(stage_options.keys())
    if st.session_state.get("calculator_stage_key") not in stage_keys:
        st.session_state["calculator_stage_key"] = stage_keys[0]
    stage_key = input_1.selectbox(
        "Текущая стадия",
        stage_keys,
        format_func=stage_options.get,
        key="calculator_stage_key",
    )
    stop_points_key = f"calculator_stop_points_{stage_key}"
    if stop_points_key not in st.session_state:
        st.session_state[stop_points_key] = float(st.session_state.get(f"hedge_margin_stop_points_{stage_key}", 100.0))
    stop_points = float(
        input_1.number_input(
            "Стоп, пункты",
            value=float(st.session_state[stop_points_key]),
            min_value=1.0,
            step=10.0,
            key=stop_points_key,
        )
    )
    previous_stage_key = st.session_state.get("calculator_previous_stage_key", stage_key)
    model_stage_key = _model_stage_key(stage_key)
    max_risk = _stage_max_risk(prop_firm, stage_key)
    trade_risk_key = f"calculator_trade_risk_applied_{stage_key}"
    trade_risk_default_key = f"calculator_trade_risk_default_{stage_key}"
    if (
        trade_risk_key not in st.session_state
        or st.session_state.get(trade_risk_default_key) != max_risk
    ):
        st.session_state[trade_risk_key] = float(max_risk)
        st.session_state[trade_risk_default_key] = float(max_risk)

    if "calculator_current_prop_pnl" not in st.session_state:
        st.session_state["calculator_current_prop_pnl"] = 0.0
    completed_spent_key = "calculator_completed_personal_spent"
    if completed_spent_key not in st.session_state:
        st.session_state[completed_spent_key] = 0.0

    def reset_calculator_account() -> None:
        st.session_state["calculator_current_prop_pnl"] = 0.0
        st.session_state[completed_spent_key] = 0.0
        st.session_state["calculator_funded_next_start_balance"] = initial_personal_balance
        st.session_state["calculator_stage_key"] = stage_keys[0]
        for reset_stage_key in stage_keys:
            st.session_state[f"calculator_largest_winning_trade_{reset_stage_key}"] = 0.0
            st.session_state[f"calculator_trailing_high_watermark_{reset_stage_key}"] = 0.0

    current_prop_pnl = _pnl_after_stage_change(
        account_type=account_type,
        previous_stage_key=str(previous_stage_key),
        current_stage_key=stage_key,
        current_prop_pnl=float(st.session_state.get("calculator_current_prop_pnl", 0.0)),
    )
    st.session_state["calculator_current_prop_pnl"] = current_prop_pnl
    st.session_state["calculator_previous_stage_key"] = stage_key
    target_enabled_for_stage = not _is_funded_stage_key(stage_key) or funded_target_enabled
    stage_profit_target = _stage_profit_target(prop_firm, stage_key)
    current_prop_pnl = _cap_prop_pnl_to_target(
        current_prop_pnl,
        target_enabled=target_enabled_for_stage,
        profit_target=stage_profit_target,
    )
    st.session_state["calculator_current_prop_pnl"] = current_prop_pnl
    drawdown_mode = _stage_drawdown_mode(prop_firm, stage_key)
    risk_strategy = "Вручную"
    recommended_prop_risk = float(st.session_state[trade_risk_key])
    recommended_risk_reason = ""
    if account_type == "instant" and drawdown_mode == "trailing":
        risk_strategy = st.selectbox(
            "Стратегия риска пропа",
            ["Вручную", "Экономный trailing"],
            key="calculator_prop_risk_strategy",
        )
        recommended_prop_risk, recommended_risk_reason = _economic_trailing_prop_risk(
            max_risk_per_trade=max_risk,
            nominal_balance=prop_firm.nominal_balance,
            current_prop_pnl=current_prop_pnl,
            profit_target=prop_firm.funded.profit_target_for_first_payout,
            consistency_enabled=bool(st.session_state.get("instant_consistency_enabled", False)),
            consistency_percent=float(st.session_state.get("instant_consistency", 0.0)),
            minimum_days_enabled=bool(st.session_state.get("minimum_profitable_days_enabled", False)),
            minimum_day_percent=float(st.session_state.get("minimum_profitable_day_percent", 0.0)),
            minimum_days_required=int(st.session_state.get("minimum_profitable_days_required", 0)),
        )

    with input_2.form(f"calculator_trade_risk_form_{stage_key}", border=False):
        entered_trade_risk = st.number_input(
            "Риск пропа в сделке, $",
            value=_trade_risk_input_value(
                risk_strategy,
                manual_risk=float(st.session_state[trade_risk_key]),
                recommended_risk=recommended_prop_risk,
            ),
            min_value=1.0,
            step=100.0,
            disabled=risk_strategy == "Экономный trailing",
        )
        trade_risk_submitted = st.form_submit_button("Применить", use_container_width=True)
    if trade_risk_submitted and risk_strategy == "Вручную":
        st.session_state[trade_risk_key] = float(entered_trade_risk)
    manual_trade_prop_risk = float(st.session_state[trade_risk_key])
    current_trade_prop_risk = _prop_risk_for_strategy(
        risk_strategy,
        manual_risk=manual_trade_prop_risk,
        recommended_risk=recommended_prop_risk,
    )
    stage_prop_risk_percent = _risk_percent_from_amount(current_trade_prop_risk, prop_firm.nominal_balance)

    current_prop_pnl_raw = input_3.number_input(
        "Текущий PnL пропа, $",
        step=float(current_trade_prop_risk),
        key="calculator_current_prop_pnl",
    )
    current_prop_pnl = float(st.session_state.get("calculator_current_prop_pnl", current_prop_pnl_raw))
    input_2.caption(f"Сейчас применяется: {_money(current_trade_prop_risk)}")
    if risk_strategy == "Экономный trailing":
        st.info(_escape_markdown_dollars(f"Авто-риск пропа: {_money(current_trade_prop_risk)}. {recommended_risk_reason}"))
    execution_buffer_mode = st.selectbox(
        "Execution buffer",
        ["off", "light_5", "normal_10", "safety_15"],
        format_func={
            "off": "Без buffer",
            "light_5": "5% spread/slippage",
            "normal_10": "10% spread/slippage",
            "safety_15": "15% spread/slippage",
        }.get,
        key="calculator_execution_buffer_mode",
    )
    trailing_high_watermark = max(0.0, current_prop_pnl)
    trailing_key = None
    largest_trade_key = f"calculator_largest_winning_trade_{stage_key}"
    if largest_trade_key not in st.session_state:
        st.session_state[largest_trade_key] = 0.0
    st.session_state[largest_trade_key] = _updated_largest_winning_trade(
        previous_largest=float(st.session_state[largest_trade_key]),
        current_prop_pnl=current_prop_pnl,
        current_trade_prop_risk=current_trade_prop_risk,
    )
    if drawdown_mode == "trailing":
        trailing_key = f"calculator_trailing_high_watermark_{stage_key}"
        if trailing_key not in st.session_state:
            st.session_state[trailing_key] = max(0.0, current_prop_pnl)
        st.session_state[trailing_key] = max(float(st.session_state[trailing_key]), current_prop_pnl)
        trailing_high_watermark = float(st.session_state[trailing_key])

    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=prop_firm,
        stage_key=model_stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=stage_prop_risk_percent,
        mode=coverage_mode,
        trailing_risk_mode=trailing_risk_mode,
        trailing_high_watermark=trailing_high_watermark,
    )
    stage_starting_personal_balance = float(personal_balance_state["Старт личного счета на стадии, $"])
    current_personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    if stage_key == "funded" and not hedge_funded:
        current_personal_balance = stage_starting_personal_balance
    if stage_key == "funded_next":
        funded_next_start_key = "calculator_funded_next_start_balance"
        if funded_next_start_key not in st.session_state:
            st.session_state[funded_next_start_key] = initial_personal_balance
        funded_next_start_balance = float(st.session_state.get(funded_next_start_key, initial_personal_balance))
        continuation = _funded_continuation_cycle(
            nominal_balance=prop_firm.nominal_balance,
            max_loss=_stage_max_loss(prop_firm, "funded"),
            profit_target=prop_firm.funded.profit_target_for_first_payout,
            prop_risk=_stage_max_risk(prop_firm, "funded"),
            personal_balance_after_payout=funded_next_start_balance,
            protection_percent=1.0,
        )
        funded_next_ratio = float(continuation["Цель прибыли личного при сливе, $"]) / _positive_amount(_stage_max_loss(prop_firm, "funded"), 1.0)
        current_personal_balance = round(funded_next_start_balance - current_prop_pnl * funded_next_ratio, 2)
    current_stage_personal_spent = float(personal_balance_state["Фактический hedge-loss, $"])
    if stage_key == "funded_next":
        current_stage_personal_spent = max(0.0, funded_next_start_balance - current_personal_balance)
    completed_personal_spent = float(st.session_state.get(completed_spent_key, 0.0)) if account_type == "challenge" else 0.0
    personal_spent = _total_personal_spent(
        completed_spent=completed_personal_spent,
        current_stage_spent=current_stage_personal_spent,
    )

    daily_loss_limit = _stage_daily_loss(prop_firm, stage_key)
    trade = calculate_personal_risk_for_trade(
        config=prop_firm,
        stage_key=model_stage_key,
        current_prop_pnl=current_prop_pnl,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=current_personal_balance,
        prop_risk_percent=stage_prop_risk_percent,
        mode=coverage_mode,
        max_risk_per_trade=current_trade_prop_risk,
        target_enabled=target_enabled_for_stage,
        daily_loss_limit=daily_loss_limit,
        hedge_funded=hedge_funded,
        trailing_high_watermark=trailing_high_watermark,
        trailing_risk_mode=trailing_risk_mode,
    )

    if stage_key == "funded_next":
        continuation_trade_personal_risk = round(float(trade["Риск пропа, $"]) * funded_next_ratio, 2)
        trade["Риск личного, $"] = continuation_trade_personal_risk
        trade["Цель личного счета при потере пропа, $"] = round(funded_next_start_balance + float(continuation["Цель прибыли личного при сливе, $"]), 2)
        trade["Ожидаемый личный счет при потере пропа, $"] = round(funded_next_start_balance + float(continuation["Цель прибыли личного при сливе, $"]), 2)
        trade["personal_risk_percent_of_prop"] = round(funded_next_ratio * 100, 2)
        trade["prop_to_personal_risk_multiple"] = round(1 / funded_next_ratio, 2) if funded_next_ratio > 0 else 0.0

    base_personal_risk = float(trade["Риск личного, $"])
    buffered_personal_risk = _personal_risk_with_execution_buffer(base_personal_risk, execution_buffer_mode)
    target_reached = bool(target_enabled_for_stage and float(trade["distance_to_target"]) <= 0.0)
    next_stage_key = _next_stage_key(account_type, stage_key, stage_options) if target_reached else None
    next_stage_label = _next_stage_label(next_stage_key) if target_reached else None
    risk_1, risk_2, risk_3 = st.columns(3)
    risk_1.metric("Риск пропа", _money(float(trade["Риск пропа, $"])))
    risk_1.markdown(f"**Лот: {_lot_from_risk_and_stop_points(float(trade['Риск пропа, $']), stop_points):.2f}**")
    if target_reached:
        risk_2.metric("Риск личного", next_stage_label)
        risk_2.caption("после перехода PnL сбросится на 0")
    else:
        risk_2.metric("Риск личного", _money(buffered_personal_risk))
        risk_2.markdown(f"**Лот: {_lot_from_risk_and_stop_points(buffered_personal_risk, stop_points):.2f}**")
        risk_2.caption(
            _hedge_multiple_display(
                float(trade["Риск пропа, $"]) / buffered_personal_risk if buffered_personal_risk > 0 else 0.0
            )
        )
    if not target_reached and buffered_personal_risk != base_personal_risk:
        risk_2.caption(f"Расчетный {_money(base_personal_risk)}")
    risk_3.metric("Потрачено личных", _money(personal_spent))

    status_1, status_2, status_3 = st.columns(3)
    status_1.metric("Баланс личного", _money(current_personal_balance))
    status_2.metric("Осталось до цели", _target_distance_display(target_enabled_for_stage, float(trade["distance_to_target"])))
    status_3.metric("Осталось до max loss", _money(float(trade["distance_to_max_loss"])))

    if drawdown_mode == "trailing" and trailing_key is not None:
        trailing_info, trailing_reset = st.columns([4, 1])
        trailing_info.info(
            _escape_markdown_dollars(
                _trailing_drawdown_display(
                    nominal_balance=prop_firm.nominal_balance,
                    trailing_high_watermark=trailing_high_watermark,
                    max_loss=_stage_max_loss(prop_firm, stage_key),
                )
            )
        )
        trailing_reset.button(
            "Сбросить счет",
            key=f"reset_trailing_account_{stage_key}",
            use_container_width=True,
            on_click=reset_calculator_account,
        )
    else:
        _, account_reset = st.columns([4, 1])
        account_reset.button(
            "Сбросить счет",
            key=f"reset_account_{account_type}_{stage_key}",
            use_container_width=True,
            on_click=reset_calculator_account,
        )

    consistency_enabled_key, consistency_percent_key = _consistency_state_keys(account_type, stage_key)
    consistency_status = _consistency_status_display(
        enabled=bool(st.session_state.get(consistency_enabled_key, False)),
        rule_percent=float(st.session_state.get(consistency_percent_key, 0.0)),
        current_prop_pnl=current_prop_pnl,
        largest_profit=float(st.session_state.get(largest_trade_key, 0.0)),
    )
    if consistency_status is not None:
        consistency_level, consistency_message = consistency_status
        if consistency_level == "success":
            st.success(_escape_markdown_dollars(consistency_message))
        elif consistency_level == "warning":
            st.warning(_escape_markdown_dollars(consistency_message))
        else:
            st.info(_escape_markdown_dollars(consistency_message))

    minimum_days_status = _minimum_profitable_days_status_display(
        enabled=stage_key == "funded" and bool(st.session_state.get("minimum_profitable_days_enabled", False)),
        required_days=int(st.session_state.get("minimum_profitable_days_required", 0)),
        completed_days=_profitable_days_from_pnl(
            current_prop_pnl=current_prop_pnl,
            minimum_day_profit=prop_firm.nominal_balance * float(st.session_state.get("minimum_profitable_day_percent", 0.0)) / 100,
            required_days=int(st.session_state.get("minimum_profitable_days_required", 0)),
        ),
        minimum_day_profit=prop_firm.nominal_balance * float(st.session_state.get("minimum_profitable_day_percent", 0.0)) / 100,
    )
    if minimum_days_status is not None:
        minimum_days_level, minimum_days_message = minimum_days_status
        if minimum_days_level == "success":
            st.success(_escape_markdown_dollars(minimum_days_message))
        else:
            st.warning(_escape_markdown_dollars(minimum_days_message))

    if target_enabled_for_stage:
        if target_reached and next_stage_key is not None:
            def move_to_next_stage() -> None:
                st.session_state[completed_spent_key] = personal_spent
                if next_stage_key == "funded_next":
                    payout_after_split = max(0.0, current_prop_pnl) * prop_firm.funded.trader_split
                    st.session_state["calculator_funded_next_start_balance"] = current_personal_balance + payout_after_split
                st.session_state["calculator_current_prop_pnl"] = 0.0
                st.session_state["calculator_stage_key"] = next_stage_key
                st.session_state[f"calculator_largest_winning_trade_{next_stage_key}"] = 0.0
                st.session_state[f"calculator_trailing_high_watermark_{next_stage_key}"] = 0.0

            transition_info, transition_action = st.columns([4, 1])
            transition_info.success(f"Цель достигнута: дальше {next_stage_label}.")
            transition_action.button(
                "Перейти",
                key=f"move_to_next_stage_{stage_key}_{next_stage_key}",
                use_container_width=True,
                on_click=move_to_next_stage,
            )
        elif target_reached:
            st.success("Цель достигнута")
        else:
            st.success(str(trade["target_status"]))
    if consider_news:
        st.info(f"Новости учитываются как forced close, а не как штраф. Текущий сценарий закрытия: {forced_close_r:.2f}R.")

    next_personal_risk = 0.0 if target_reached else buffered_personal_risk
    next_prop_risk = 0.0 if target_reached else float(trade["Риск пропа, $"])

    liquidity_preview = None
    if bool(st.session_state.get("hedge_margin_check_enabled", False)):
        preview_extra_liquidity = float(st.session_state.get(f"hedge_margin_extra_liquidity_{stage_key}", 0.0))
        preview_broker_deposit = _synced_broker_deposit(
            current_personal_balance=current_personal_balance,
            extra_liquidity=preview_extra_liquidity,
        )
        liquidity_preview = _hedge_margin_liquidity(
            personal_risk=next_personal_risk,
            stop_points_5_digit=stop_points,
            leverage=float(st.session_state.get(f"hedge_margin_leverage_{stage_key}", 300.0)),
            eurusd_price=float(st.session_state.get(f"hedge_margin_eurusd_price_{stage_key}", 1.14)),
            broker_deposit=preview_broker_deposit,
            spread_points_5_digit=float(st.session_state.get(f"hedge_margin_spread_points_{stage_key}", 0.0)),
            commission_per_million_per_side=float(st.session_state.get(f"hedge_margin_commission_{stage_key}", 10.0)),
            stop_out_percent=float(st.session_state.get(f"hedge_margin_stop_out_{stage_key}", 50.0)),
        )
        if liquidity_preview["Докинуть под маржу, $"] > 0 or liquidity_preview["Докинуть чтобы стоп выдержал, $"] > 0:
            st.warning(
                "Ликвидности не хватает: это пополнение только чтобы открыть/удержать hedge до стопа, не новый риск. "
                "[Открыть расчет ликвидности](#liquidity-personal-hedge)"
            )

    if stage_key == "funded_next":
        cycle_result = _funded_next_cycle_result(
            current_prop_pnl=current_prop_pnl,
            current_personal_balance=current_personal_balance,
            funded_next_start_balance=funded_next_start_balance,
            trader_split=prop_firm.funded.trader_split,
        )
        result_1, result_2 = st.columns(2)
        result_1.metric("Профит на funded", _money(cycle_result["Профит на funded, $"]))
        result_2.metric("К выплате после сплита", _money(cycle_result["К выплате после сплита, $"]))
        result_3, result_4 = st.columns(2)
        result_3.metric("Результат hedge", _money(cycle_result["Результат hedge, $"]))
        result_4.metric("Итог цикла", _money(cycle_result["Итог цикла, $"]))
    elif _is_funded_stage_key(stage_key) or funded_target_enabled:
        payout_profit = current_prop_pnl if _is_funded_stage_key(stage_key) else prop_firm.funded.profit_target_for_first_payout
        payout = calculate_funded_payout_preview(
            config=prop_firm,
            initial_personal_balance=initial_personal_balance,
            prop_risk_percent=prop_risk_percent,
            funded_profit=payout_profit,
            mode=coverage_mode,
            hedge_funded=hedge_funded,
            trailing_risk_mode=trailing_risk_mode,
        )
        payout_1, payout_2, payout_3 = st.columns(3)
        payout_profit_label = "Профит на instant" if account_type == "instant" else "Профит на funded"
        payout_1.metric(payout_profit_label, _money(payout["Профит на funded, $"]))
        payout_2.metric("К выплате после сплита", _money(payout["К выплате после сплита, $"]))
        payout_3.metric("Чистыми", _money(payout["Чистыми после личных затрат, $"]))
        cleanest_net = float(payout["К выплате после сплита, $"]) + current_personal_balance - initial_personal_balance - prop_firm.challenge_fee
        st.metric("Чистейшими", _money(cleanest_net))

        if not hedge_funded:
            st.caption("Выплатной этап не хеджируется: личный счет фиксируется, а чистыми считается payout минус затраты на счет.")

        personal_balance_after_payout = current_personal_balance + float(payout["К выплате после сплита, $"])
        continuation = _funded_continuation_cycle(
            nominal_balance=prop_firm.nominal_balance,
            max_loss=_stage_max_loss(prop_firm, "funded"),
            profit_target=prop_firm.funded.profit_target_for_first_payout,
            prop_risk=_stage_max_risk(prop_firm, "funded"),
            personal_balance_after_payout=personal_balance_after_payout,
            protection_percent=1.0,
        )
        st.subheader("Следующий funded-цикл")
        cycle_1, cycle_2, cycle_3, cycle_4 = st.columns(4)
        cycle_1.metric("Нужно докинуть", _money(continuation["Нужно докинуть, $"]))
        cycle_2.metric("Депозит на цикл", _money(continuation["Депозит на следующий цикл, $"]))
        cycle_3.metric("Риск личного", _money(continuation["Риск личного на сделку, $"]))
        cycle_4.metric("При сливе личный", _money(continuation["Личный баланс при сливе, $"]))
        st.caption(
            _escape_markdown_dollars(
                f"Цель: при потере funded-счета личный счет получает +{_money(continuation['Цель прибыли личного при сливе, $'])} "
                f"({continuation['Защитная цель, %']:.2f}% от проп-счета)."
            )
        )

    deal_table = pd.DataFrame(
        [
            {
                "PnL пропа": _money(current_prop_pnl),
                "Потрачено личных": _money(personal_spent),
                "Осталось до цели": _target_distance_display(target_enabled_for_stage, float(trade["distance_to_target"])),
                "Осталось до max loss": _money(float(trade["distance_to_max_loss"])),
                "Следующий риск пропа": _money(next_prop_risk),
                "Следующий риск личного": next_stage_label if target_reached else _money(next_personal_risk),
                "Баланс личного после Win": _money(current_personal_balance - next_personal_risk),
                "Баланс личного после Loss": _money(current_personal_balance + next_personal_risk),
            }
        ]
    )
    st.dataframe(deal_table, use_container_width=True, hide_index=True)

    if bool(st.session_state.get("hedge_margin_check_enabled", False)):
        st.markdown('<div id="liquidity-personal-hedge"></div>', unsafe_allow_html=True)
        with st.expander("Ликвидность для личного hedge", expanded=True):
            liquidity_input_1, liquidity_input_2 = st.columns(2)
            extra_liquidity = liquidity_input_1.number_input(
                "Доп. ликвидность, $",
                value=0.0,
                min_value=0.0,
                step=25.0,
                key=f"hedge_margin_extra_liquidity_{stage_key}",
            )
            broker_deposit = _synced_broker_deposit(
                current_personal_balance=current_personal_balance,
                extra_liquidity=float(extra_liquidity),
            )
            liquidity_input_1.caption(f"Депозит у брокера: {_money(broker_deposit)} = баланс личного + доп. ликвидность")
            liquidity_input_2.caption(f"Стоп из калькулятора: {stop_points:.0f} пунктов")
            leverage = liquidity_input_2.number_input(
                "Плечо",
                value=300.0,
                min_value=1.0,
                step=100.0,
                key=f"hedge_margin_leverage_{stage_key}",
            )
            cost_input_1, cost_input_2, cost_input_3, cost_input_4 = st.columns(4)
            eurusd_price = cost_input_1.number_input(
                "EURUSD цена для маржи",
                value=1.14,
                min_value=0.1,
                step=0.01,
                format="%.5f",
                key=f"hedge_margin_eurusd_price_{stage_key}",
            )
            spread_points = cost_input_2.number_input(
                "Спред, пункты 5-знака",
                value=0.0,
                min_value=0.0,
                step=1.0,
                key=f"hedge_margin_spread_points_{stage_key}",
            )
            commission = cost_input_3.number_input(
                "Комиссия $/mio/сторона",
                value=10.0,
                min_value=0.0,
                step=1.0,
                key=f"hedge_margin_commission_{stage_key}",
            )
            stop_out_percent = cost_input_4.number_input(
                "Stop Out, %",
                value=50.0,
                min_value=0.0,
                max_value=100.0,
                step=10.0,
                key=f"hedge_margin_stop_out_{stage_key}",
            )
            liquidity = _hedge_margin_liquidity(
                personal_risk=next_personal_risk,
                stop_points_5_digit=stop_points,
                leverage=float(leverage),
                eurusd_price=float(eurusd_price),
                broker_deposit=float(broker_deposit),
                spread_points_5_digit=float(spread_points),
                commission_per_million_per_side=float(commission),
                stop_out_percent=float(stop_out_percent),
            )
            liquidity_1, liquidity_2, liquidity_3, liquidity_4 = st.columns(4)
            liquidity_1.metric("Лот hedge", f"{liquidity['Лот hedge']:.2f}")
            liquidity_2.metric("Маржа нужна", _money(liquidity["Маржа нужна, $"]))
            liquidity_3.metric("Критический equity", _money(liquidity["Критический equity Stop Out, $"]))
            liquidity_4.metric("Equity после стопа", _money(liquidity["Equity после стопа, $"]))
            stopout_1, stopout_2, stopout_3, stopout_4 = st.columns(4)
            stopout_1.metric("Запас до Stop Out", _money(liquidity["Запас до Stop Out после стопа, $"]))
            stopout_2.metric("Докинуть для открытия", _money(liquidity["Докинуть под маржу, $"]))
            stopout_3.metric("Докинуть до стопа", _money(liquidity["Докинуть чтобы стоп выдержал, $"]))
            stopout_4.metric("Комиссия+спред", _money(liquidity["Комиссия+спред, $"]))
            if liquidity["Докинуть под маржу, $"] <= 0 and liquidity["Докинуть чтобы стоп выдержал, $"] <= 0:
                st.success(_escape_markdown_dollars(f"Маржи хватает. Свободно после маржи: {_money(liquidity['Свободно после маржи, $'])}."))

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
    if _is_funded_stage_key(stage_key):
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


def _total_personal_spent(completed_spent: float, current_stage_spent: float) -> float:
    return round(max(0.0, float(completed_spent)) + max(0.0, float(current_stage_spent)), 2)


def _hedge_summary_display(multiplier: float, personal_percent: float) -> str:
    if multiplier <= 0:
        return "нет хеджа"
    compact_multiplier = f"{multiplier:.0f}x" if multiplier.is_integer() else f"{multiplier:.1f}x"
    return f"{personal_percent:.2f}% от пропа · {compact_multiplier} меньше"


def _hedge_multiple_display(multiplier: float) -> str:
    if multiplier <= 0:
        return "нет хеджа"
    return f"{multiplier:.0f}x" if float(multiplier).is_integer() else f"{multiplier:.1f}x"


def _lot_from_risk_and_stop_points(risk_amount: float, stop_points_5_digit: float) -> float:
    stop_points_5_digit = float(stop_points_5_digit)
    if stop_points_5_digit <= 0:
        return 0.0
    return round(max(0.0, float(risk_amount)) / stop_points_5_digit, 2)


def _default_prop_risk_percent(config: PropFirmConfig) -> float:
    if _account_type(config) == "instant":
        return _stage_risk_percent(config, "funded")
    return _stage_risk_percent(config, "phase_1")


def _stage_options(config: PropFirmConfig) -> dict[str, str]:
    if _account_type(config) == "instant":
        return {"funded": "Instant счет", "funded_next": "Funded после выплаты"}
    return {
        **{f"phase_{index + 1}": f"Этап {index + 1}: {stage.name}" for index, stage in enumerate(config.stages)},
        "funded": "Funded до первой выплаты",
        "funded_next": "Funded после выплаты",
    }


def _next_stage_key(account_type: str, stage_key: str, stage_options: dict[str, str]) -> str | None:
    if account_type != "challenge":
        return None
    keys = list(stage_options.keys())
    if stage_key not in keys:
        return None
    next_index = keys.index(stage_key) + 1
    if next_index >= len(keys):
        return None
    return keys[next_index]


def _next_stage_label(next_stage_key: str | None) -> str:
    if next_stage_key is None:
        return "Цель достигнута"
    if next_stage_key == "funded":
        return "Funded"
    if next_stage_key == "funded_next":
        return "Funded после выплаты"
    if next_stage_key.startswith("phase_"):
        return f"{next_stage_key.replace('phase_', '')}-я фаза"
    return next_stage_key


def _account_type(config) -> str:
    return getattr(config, "account_type", "challenge")


def _has_trailing_drawdown(config: PropFirmConfig) -> bool:
    return any(_field(stage, "drawdown_mode", "static") == "trailing" for stage in config.stages) or _field(
        config.funded,
        "drawdown_mode",
        "static",
    ) == "trailing"


def _enabled_tab_labels() -> list[str]:
    return [
        "Калькулятор сделки",
        "Симуляции / Monte Carlo",
        "Принципы выбора пропа",
    ]


def _stage_daily_loss(config: PropFirmConfig, stage_key: str) -> float | None:
    if _is_funded_stage_key(stage_key):
        return _field(config.funded, "daily_loss", None)
    return _field(config.stages[int(stage_key.replace("phase_", "")) - 1], "daily_loss", None)


def _stage_max_loss(config: PropFirmConfig, stage_key: str) -> float:
    if _is_funded_stage_key(stage_key):
        return float(_field(config.funded, "max_loss", 0.0))
    return float(_field(config.stages[int(stage_key.replace("phase_", "")) - 1], "max_loss", 0.0))


def _stage_drawdown_mode(config: PropFirmConfig, stage_key: str) -> str:
    if _is_funded_stage_key(stage_key):
        return str(_field(config.funded, "drawdown_mode", "static"))
    return str(_field(config.stages[int(stage_key.replace("phase_", "")) - 1], "drawdown_mode", "static"))


def _stage_profit_target(config: PropFirmConfig, stage_key: str) -> float:
    if _is_funded_stage_key(stage_key):
        return float(_field(config.funded, "profit_target_for_first_payout", 0.0))
    return float(_field(config.stages[int(stage_key.replace("phase_", "")) - 1], "profit_target", 0.0))


def _is_funded_stage_key(stage_key: str) -> bool:
    return stage_key in {"funded", "funded_next"}


def _model_stage_key(stage_key: str) -> str:
    return "funded" if _is_funded_stage_key(stage_key) else stage_key


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


def _cap_prop_pnl_to_target(current_prop_pnl: float, target_enabled: bool, profit_target: float) -> float:
    current_prop_pnl = float(current_prop_pnl)
    profit_target = float(profit_target)
    if not target_enabled or profit_target <= 0:
        return current_prop_pnl
    return min(current_prop_pnl, profit_target)


def _pnl_after_stage_change(
    account_type: str,
    previous_stage_key: str,
    current_stage_key: str,
    current_prop_pnl: float,
) -> float:
    if account_type == "challenge" and previous_stage_key != current_stage_key:
        return 0.0
    return float(current_prop_pnl)


def _trailing_drawdown_display(nominal_balance: float, trailing_high_watermark: float, max_loss: float) -> str:
    max_balance = nominal_balance + max(0.0, trailing_high_watermark)
    failure_balance = max_balance - max_loss
    return f"Trailing max {_money(max_balance)} · линия слива {_money(failure_balance)}"


def _consistency_state_keys(account_type: str, stage_key: str) -> tuple[str, str]:
    if account_type == "instant":
        return ("instant_consistency_enabled", "instant_consistency")
    if stage_key == "funded":
        return ("funded_consistency_enabled", "funded_consistency")
    return (f"{stage_key}_consistency_enabled", f"{stage_key}_consistency")


def _consistency_status_display(
    enabled: bool,
    rule_percent: float,
    current_prop_pnl: float,
    largest_profit: float,
) -> tuple[str, str] | None:
    if not enabled:
        return None
    if rule_percent <= 0 or largest_profit <= 0:
        return ("info", "Consistency включен, но правило или прибыльная сделка пока не заданы.")
    required_profit = largest_profit / (rule_percent / 100)
    if current_prop_pnl >= required_profit:
        return (
            "success",
            f"Consistency выполнен: крупнейшая сделка {_money(largest_profit)} укладывается в {rule_percent:.2f}% от прибыли.",
        )
    remaining = required_profit - max(0.0, current_prop_pnl)
    return (
        "warning",
        f"Consistency еще не выполнен: нужен PnL {_money(required_profit)}, осталось {_money(remaining)}.",
    )


def _minimum_profitable_days_status_display(
    enabled: bool,
    required_days: int,
    completed_days: int,
    minimum_day_profit: float,
) -> tuple[str, str] | None:
    if not enabled:
        return None
    required_days = max(0, int(required_days))
    completed_days = max(0, int(completed_days))
    minimum_day_profit = max(0.0, float(minimum_day_profit))
    if required_days <= 0:
        return ("success", "Минимальные прибыльные дни не требуются.")
    if completed_days >= required_days:
        return (
            "success",
            f"Минимальные прибыльные дни выполнены: {completed_days}/{required_days} дней минимум по {_money(minimum_day_profit)}.",
        )
    remaining_days = required_days - completed_days
    day_word = _russian_day_word(remaining_days)
    return (
        "warning",
        f"Минимальные прибыльные дни: выполнено {completed_days}/{required_days}. Нужно еще {remaining_days} {day_word} минимум по {_money(minimum_day_profit)}.",
    )


def _profitable_days_from_pnl(current_prop_pnl: float, minimum_day_profit: float, required_days: int) -> int:
    if current_prop_pnl <= 0 or minimum_day_profit <= 0 or required_days <= 0:
        return 0
    return min(int(required_days), int(current_prop_pnl // minimum_day_profit))


def _economic_trailing_prop_risk(
    max_risk_per_trade: float,
    nominal_balance: float,
    current_prop_pnl: float,
    profit_target: float,
    consistency_enabled: bool,
    consistency_percent: float,
    minimum_days_enabled: bool,
    minimum_day_percent: float,
    minimum_days_required: int,
) -> tuple[float, str]:
    max_risk = _positive_amount(float(max_risk_per_trade), 1.0)
    consistency_cap = (
        max(1.0, float(profit_target) * float(consistency_percent) / 100)
        if consistency_enabled and consistency_percent > 0 and profit_target > 0
        else max_risk
    )
    start_risk = round(min(max_risk, consistency_cap), 2)
    minimum_day_profit = nominal_balance * max(0.0, float(minimum_day_percent)) / 100
    middle_risk = round(min(max_risk, minimum_day_profit), 2) if minimum_days_enabled and minimum_day_profit > 0 else round(min(max_risk, start_risk * 2 / 3), 2)
    middle_risk = _positive_amount(middle_risk, start_risk)
    finish_risk = round(max(1.0, min(middle_risk / 2, max_risk)), 2)
    completed_days = _profitable_days_from_pnl(current_prop_pnl, minimum_day_profit, minimum_days_required)

    if current_prop_pnl < 0:
        return round(max_risk, 2), "Проп в минусе: полный риск помогает личному хеджу вернуть деньги."
    if current_prop_pnl < start_risk:
        return start_risk, "Стартовый риск ограничен consistency, чтобы крупнейшая прибыльная сделка не раздувала нужный target."
    if minimum_days_enabled and completed_days < minimum_days_required:
        return middle_risk, "Добиваем минимальные прибыльные дни минимальным нужным риском."
    return finish_risk, "Защитный режим после набора буфера: не разгоняем trailing high watermark."


def _prop_risk_for_strategy(strategy: str, manual_risk: float, recommended_risk: float) -> float:
    if strategy == "Экономный trailing":
        return round(_positive_amount(float(recommended_risk), 1.0), 2)
    return round(_positive_amount(float(manual_risk), 1.0), 2)


def _personal_risk_with_execution_buffer(personal_risk: float, buffer_mode: str) -> float:
    multipliers = {
        "off": 1.0,
        "light_5": 1.05,
        "normal_10": 1.10,
        "safety_15": 1.15,
    }
    multiplier = multipliers.get(buffer_mode, 1.0)
    return round(max(0.0, float(personal_risk)) * multiplier, 2)


def _funded_continuation_cycle(
    nominal_balance: float,
    max_loss: float,
    profit_target: float,
    prop_risk: float,
    personal_balance_after_payout: float,
    protection_percent: float,
) -> dict[str, float]:
    prop_risk = _positive_amount(float(prop_risk), 1.0)
    max_loss = _positive_amount(float(max_loss), prop_risk)
    profit_target = max(0.0, float(profit_target))
    personal_balance_after_payout = max(0.0, float(personal_balance_after_payout))
    protection_percent = max(0.0, float(protection_percent))
    target_personal_profit = float(nominal_balance) * protection_percent / 100
    loss_trades_to_failure = max_loss / prop_risk
    win_trades_to_payout = profit_target / prop_risk if prop_risk > 0 else 0.0
    personal_risk = target_personal_profit / loss_trades_to_failure if loss_trades_to_failure > 0 else 0.0
    cycle_deposit = personal_risk * win_trades_to_payout
    top_up = max(0.0, cycle_deposit - personal_balance_after_payout)
    cycle_start_balance = personal_balance_after_payout + top_up
    return {
        "Защитная цель, %": round(protection_percent, 2),
        "Цель прибыли личного при сливе, $": round(target_personal_profit, 2),
        "Риск личного на сделку, $": round(personal_risk, 2),
        "Депозит на следующий цикл, $": round(cycle_deposit, 2),
        "Нужно докинуть, $": round(top_up, 2),
        "Личный баланс после выплаты, $": round(personal_balance_after_payout, 2),
        "Личный баланс при сливе, $": round(cycle_start_balance + target_personal_profit, 2),
    }


def _funded_next_cycle_result(
    current_prop_pnl: float,
    current_personal_balance: float,
    funded_next_start_balance: float,
    trader_split: float,
) -> dict[str, float]:
    funded_profit = max(0.0, float(current_prop_pnl))
    payout_after_split = funded_profit * min(1.0, max(0.0, float(trader_split)))
    hedge_result = float(current_personal_balance) - float(funded_next_start_balance)
    return {
        "Профит на funded, $": round(funded_profit, 2),
        "К выплате после сплита, $": round(payout_after_split, 2),
        "Результат hedge, $": round(hedge_result, 2),
        "Итог цикла, $": round(payout_after_split + hedge_result, 2),
    }


def _hedge_margin_liquidity(
    personal_risk: float,
    stop_points_5_digit: float,
    leverage: float,
    eurusd_price: float,
    broker_deposit: float,
    spread_points_5_digit: float,
    commission_per_million_per_side: float,
    stop_out_percent: float = 50.0,
) -> dict[str, float]:
    personal_risk = max(0.0, float(personal_risk))
    stop_pips = max(0.1, float(stop_points_5_digit) / 10)
    spread_pips = max(0.0, float(spread_points_5_digit) / 10)
    leverage = _positive_amount(float(leverage), 1.0)
    eurusd_price = _positive_amount(float(eurusd_price), 1.0)
    broker_deposit = max(0.0, float(broker_deposit))
    commission_per_million_per_side = max(0.0, float(commission_per_million_per_side))
    stop_out_percent = min(100.0, max(0.0, float(stop_out_percent)))
    commission_round_turn_per_lot = commission_per_million_per_side * 0.2
    stop_risk_per_lot = stop_pips * 10
    spread_cost_per_lot = spread_pips * 10
    total_cost_per_lot = stop_risk_per_lot + spread_cost_per_lot + commission_round_turn_per_lot
    hedge_lot = personal_risk / total_cost_per_lot if total_cost_per_lot > 0 else 0.0
    margin_required = hedge_lot * 100_000 * eurusd_price / leverage
    execution_cost = hedge_lot * (spread_cost_per_lot + commission_round_turn_per_lot)
    margin_topup = max(0.0, margin_required - broker_deposit)
    stopout_equity = margin_required * stop_out_percent / 100
    equity_after_stop = broker_deposit - personal_risk
    stopout_buffer_after_stop = equity_after_stop - stopout_equity
    stopout_topup = max(0.0, stopout_equity + personal_risk - broker_deposit)
    free_after_margin = broker_deposit - margin_required
    return {
        "Лот hedge": round(hedge_lot, 2),
        "Маржа нужна, $": round(margin_required, 2),
        "Stop Out, %": round(stop_out_percent, 2),
        "Критический equity Stop Out, $": round(stopout_equity, 2),
        "Equity после стопа, $": round(equity_after_stop, 2),
        "Запас до Stop Out после стопа, $": round(stopout_buffer_after_stop, 2),
        "Докинуть под маржу, $": round(margin_topup, 2),
        "Докинуть чтобы стоп выдержал, $": round(stopout_topup, 2),
        "Свободно после маржи, $": round(free_after_margin, 2),
        "Комиссия+спред, $": round(execution_cost, 2),
    }


def _synced_broker_deposit(current_personal_balance: float, extra_liquidity: float) -> float:
    return round(max(0.0, float(current_personal_balance)) + max(0.0, float(extra_liquidity)), 2)


def _trade_risk_input_value(strategy: str, manual_risk: float, recommended_risk: float) -> float:
    return _prop_risk_for_strategy(strategy, manual_risk=manual_risk, recommended_risk=recommended_risk)


def _updated_largest_winning_trade(
    previous_largest: float,
    current_prop_pnl: float,
    current_trade_prop_risk: float,
) -> float:
    if current_prop_pnl <= 0:
        return max(0.0, previous_largest)
    return max(0.0, previous_largest, current_trade_prop_risk)


def _russian_day_word(value: int) -> str:
    value = abs(int(value))
    if 11 <= value % 100 <= 14:
        return "дней"
    if value % 10 == 1:
        return "день"
    if 2 <= value % 10 <= 4:
        return "дня"
    return "дней"


def _escape_markdown_dollars(text: str) -> str:
    return text.replace("$", r"\$")


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
    kwargs = dict(kwargs)
    kwargs["profit_target_for_first_payout"] = _positive_amount(float(kwargs["profit_target_for_first_payout"]), 1.0)
    kwargs["max_loss"] = _positive_amount(float(kwargs["max_loss"]), 1.0)
    if kwargs.get("daily_loss") is not None:
        kwargs["daily_loss"] = _positive_amount(float(kwargs["daily_loss"]), kwargs["max_loss"] / 2)
    kwargs["trader_split"] = min(1.0, max(0.01, float(kwargs["trader_split"])))
    if kwargs.get("max_risk_per_trade") is not None:
        kwargs["max_risk_per_trade"] = _positive_amount(float(kwargs["max_risk_per_trade"]), 1.0)
    try:
        return FundedConfig(**kwargs)
    except ValueError:
        kwargs["profit_target_for_first_payout"] = _positive_amount(float(kwargs.get("profit_target_for_first_payout", 0.0)), 1.0)
        kwargs["max_loss"] = _positive_amount(float(kwargs.get("max_loss", 0.0)), 1.0)
        kwargs["trader_split"] = min(1.0, max(0.01, float(kwargs.get("trader_split", 0.01))))
        kwargs["daily_loss"] = _positive_amount(float(kwargs.get("daily_loss") or 0.0), kwargs["max_loss"] / 2)
        kwargs["max_risk_per_trade"] = _positive_amount(float(kwargs.get("max_risk_per_trade") or 0.0), 1.0)
        try:
            return FundedConfig(**kwargs)
        except TypeError:
            return FundedConfig(
                profit_target_for_first_payout=kwargs["profit_target_for_first_payout"],
                max_loss=kwargs["max_loss"],
                trader_split=kwargs["trader_split"],
            )
    except TypeError:
        return FundedConfig(
            profit_target_for_first_payout=kwargs["profit_target_for_first_payout"],
            max_loss=kwargs["max_loss"],
            trader_split=kwargs["trader_split"],
        )


def _make_prop_firm_config(**kwargs) -> PropFirmConfig:
    try:
        return PropFirmConfig(**kwargs)
    except TypeError:
        account_type = str(kwargs.get("account_type", "challenge"))
        legacy_kwargs = _legacy_prop_firm_kwargs(kwargs)
        return _attach_account_type(PropFirmConfig(**legacy_kwargs), account_type)


def _legacy_prop_firm_kwargs(kwargs: dict) -> dict:
    legacy_kwargs = dict(kwargs)
    account_type = str(legacy_kwargs.pop("account_type", "challenge"))
    if account_type == "instant" and not legacy_kwargs.get("stages"):
        funded = legacy_kwargs["funded"]
        max_risk_per_trade = _field(funded, "max_risk_per_trade", None) or legacy_kwargs.get("prop_risk_per_trade", 0.0)
        legacy_kwargs["stages"] = [
            _make_stage_config(
                name="instant",
                profit_target=_positive_amount(float(_field(funded, "profit_target_for_first_payout", 0.0)), 1.0),
                max_loss=_positive_amount(float(_field(funded, "max_loss", 0.0)), 1.0),
                max_loss_mode=str(_field(funded, "max_loss_mode", "amount")),
                daily_loss=_field(funded, "daily_loss", None),
                daily_loss_mode=str(_field(funded, "daily_loss_mode", "amount")),
                max_risk_per_trade=_positive_amount(float(max_risk_per_trade), 1.0),
                drawdown_mode=str(_field(funded, "drawdown_mode", "static")),
            )
        ]
    return legacy_kwargs


def _attach_account_type(config, account_type: str):
    try:
        object.__setattr__(config, "account_type", account_type)
    except Exception:
        try:
            setattr(config, "account_type", account_type)
        except Exception:
            pass
    return config


def _money(value: float) -> str:
    if not math.isfinite(float(value)):
        return "недоступно"
    return f"${value:,.2f}"


def _percent(value: float) -> str:
    return f"{value:.2f}%"


def _to_amount(value: float, mode: str, nominal_balance: float) -> float:
    return value if mode == "amount" else nominal_balance * value / 100


def _from_amount(value: float, mode: str, nominal_balance: float) -> float:
    return value if mode == "amount" else value / nominal_balance * 100


def _positive_amount(value: float, fallback: float) -> float:
    if value > 0:
        return value
    if fallback > 0:
        return fallback
    return 1.0


if __name__ == "__main__":
    main()
