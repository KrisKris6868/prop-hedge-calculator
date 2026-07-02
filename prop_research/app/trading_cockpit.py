from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prop_research.app.hedge_model import (
    CoverageMode,
    TrailingRiskMode,
    calculate_personal_balance_from_prop_pnl,
    calculate_personal_risk_for_trade,
    minimum_personal_deposit_for_strict_free_prop,
)
from prop_research.app.streamlit_app import (
    _account_type,
    _consistency_state_keys,
    _default_prop_risk_percent,
    _economic_trailing_prop_risk,
    _hedge_margin_liquidity,
    _lot_from_risk_and_stop_points,
    _next_stage_key,
    _next_stage_label,
    _risk_percent_from_amount,
    _stage_max_risk,
    _stage_options,
    _stage_profit_target,
)
from prop_research.config.account_states import AccountState, delete_account_state, load_account_states, save_account_state
from prop_research.config.templates import (
    PropTemplate,
    load_prop_templates,
    prop_firm_from_template_config,
    prop_firm_to_template_config,
)
from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig

USER_ACCOUNT_STATE_PATH = PROJECT_ROOT / ".streamlit" / "account_states.json"
USER_TEMPLATE_PATH = PROJECT_ROOT / ".streamlit" / "prop_templates.json"


@dataclass(frozen=True)
class CockpitSummary:
    name: str
    account_type: str
    stage_key: str
    stage_label: str
    current_pnl: float
    prop_risk: float
    personal_risk: float
    base_personal_risk: float
    prop_lot: float
    hedge_lot: float
    personal_balance: float
    distance_to_target: float
    distance_to_max_loss: float
    margin_topup: float
    status: str
    consistency_text: str
    minimum_days_text: str
    personal_spent: float
    initial_personal_balance: float
    prop_account_size: float
    funded_profit: float
    funded_split_payout: float
    funded_net: float
    funded_cleanest: float
    trailing_line_text: str | None


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Trading Cockpit", layout="wide")
    _inject_cockpit_css(st)

    accounts = load_account_states(USER_ACCOUNT_STATE_PATH)
    selected_name = _render_sidebar(st, accounts)

    st.markdown('<div class="cockpit-title">Trading Cockpit</div>', unsafe_allow_html=True)
    st.markdown('<div class="cockpit-subtitle">Счета, риск, лотность и маржа в одном рабочем экране.</div>', unsafe_allow_html=True)

    if not accounts:
        st.markdown('<div class="empty-panel">Нет сохраненных рабочих счетов. Создай счет слева: с нуля или из шаблона.</div>', unsafe_allow_html=True)
        return

    selected_account = _selected_account(accounts, selected_name)
    if selected_account is None:
        selected_account = accounts[0]
    updated_selected_account = _render_account_workbench(st, selected_account)
    dashboard_accounts = [
        updated_selected_account if account.name == updated_selected_account.name else account
        for account in accounts
    ]
    _render_accounts_dashboard(st, pd, [build_account_summary(account) for account in dashboard_accounts])


def build_account_summary(account: AccountState) -> CockpitSummary:
    config = prop_firm_from_template_config(account.config)
    runtime = account.runtime_state
    ui_state = account.ui_state
    stage_options = _stage_options(config)
    stage_key = str(runtime.get("calculator_stage_key") or next(iter(stage_options)))
    if stage_key not in stage_options:
        stage_key = next(iter(stage_options))
    model_stage_key = _model_stage_key(stage_key)
    current_pnl = _float_state(runtime, "calculator_current_prop_pnl", 0.0)
    stop_points = _float_state(runtime, f"calculator_stop_points_{stage_key}", 100.0)
    manual_prop_risk = _float_state(runtime, f"calculator_trade_risk_applied_{stage_key}", _stage_max_risk(config, stage_key))
    prop_risk = _prop_risk_for_account_strategy(config, ui_state, stage_key, current_pnl, manual_prop_risk)
    prop_risk_percent = _risk_percent_from_amount(prop_risk, config.nominal_balance)
    trailing_mode = _trailing_mode_from_ui(ui_state)
    initial_personal_balance = _initial_personal_balance(config, prop_risk_percent, trailing_mode)
    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=config,
        stage_key=model_stage_key,
        current_prop_pnl=current_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        trailing_risk_mode=trailing_mode,
        trailing_high_watermark=_float_state(runtime, f"calculator_trailing_high_watermark_{stage_key}", max(0.0, current_pnl)),
    )
    personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    summary_initial_personal_balance = initial_personal_balance
    if stage_key == "funded_next":
        summary_initial_personal_balance = _funded_next_start_balance(runtime, initial_personal_balance)
        personal_balance = round(summary_initial_personal_balance - current_pnl * _funded_next_personal_risk_ratio(config), 2)
    current_stage_spent = max(0.0, summary_initial_personal_balance - personal_balance)
    completed_spent = _float_state(runtime, "calculator_completed_personal_spent", 0.0) if _account_type(config) == "challenge" else 0.0
    personal_spent = round(completed_spent + current_stage_spent, 2)
    trade = calculate_personal_risk_for_trade(
        config=config,
        stage_key=model_stage_key,
        current_prop_pnl=current_pnl,
        initial_personal_balance=initial_personal_balance,
        current_personal_balance=personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        max_risk_per_trade=prop_risk,
        target_enabled=True,
        daily_loss_limit=_stage_daily_loss(config, stage_key),
        hedge_funded=True,
        trailing_high_watermark=_float_state(runtime, f"calculator_trailing_high_watermark_{stage_key}", max(0.0, current_pnl)),
        trailing_risk_mode=trailing_mode,
    )
    effective_prop_risk = float(trade["Риск пропа, $"])
    base_personal_risk = _finite_amount(float(trade["Риск личного, $"]))
    if stage_key == "funded_next":
        base_personal_risk = round(effective_prop_risk * _funded_next_personal_risk_ratio(config), 2)
    personal_risk = _personal_risk_with_execution_costs(base_personal_risk, stop_points, ui_state)
    prop_lot = _lot_from_risk_and_stop_points(effective_prop_risk, stop_points)
    hedge_lot = _lot_from_risk_and_stop_points(base_personal_risk, stop_points)
    margin_topup = _margin_topup_for_runtime(runtime, stage_key, personal_balance, personal_risk, stop_points)
    distance_to_target = float(trade["distance_to_target"])
    distance_to_max_loss = float(trade["distance_to_max_loss"])
    funded_profit, funded_split_payout, funded_net, funded_cleanest = _funded_payout_values(
        config=config,
        stage_key=stage_key,
        current_pnl=current_pnl,
        personal_spent=personal_spent,
    )
    return CockpitSummary(
        name=account.name,
        account_type=_account_type(config),
        stage_key=stage_key,
        stage_label=stage_options[stage_key],
        current_pnl=current_pnl,
        prop_risk=effective_prop_risk,
        personal_risk=personal_risk,
        base_personal_risk=base_personal_risk,
        prop_lot=prop_lot,
        hedge_lot=hedge_lot,
        personal_balance=personal_balance,
        distance_to_target=distance_to_target,
        distance_to_max_loss=distance_to_max_loss,
        margin_topup=margin_topup,
        status=_decision_status(config, stage_key, distance_to_target, distance_to_max_loss, margin_topup),
        consistency_text=_consistency_text(account, stage_key, current_pnl),
        minimum_days_text=_minimum_days_text(account, config, stage_key, current_pnl),
        personal_spent=personal_spent,
        initial_personal_balance=summary_initial_personal_balance,
        prop_account_size=float(config.nominal_balance),
        funded_profit=funded_profit,
        funded_split_payout=funded_split_payout,
        funded_net=funded_net,
        funded_cleanest=funded_cleanest,
        trailing_line_text=_trailing_line_text(account, config, stage_key, current_pnl),
    )


def preview_account_state(account: AccountState, *, stage_key: str, pnl: float, stop_points: float, risk: float) -> AccountState:
    config = prop_firm_from_template_config(account.config)
    runtime = dict(account.runtime_state)
    previous_pnl = _float_state(runtime, "calculator_current_prop_pnl", 0.0)
    next_pnl = round(_cap_pnl_to_stage_target(config, stage_key, pnl), 2)
    runtime["calculator_stage_key"] = stage_key
    runtime["calculator_current_prop_pnl"] = next_pnl
    runtime[f"calculator_stop_points_{stage_key}"] = round(float(stop_points), 2)
    runtime[f"calculator_trade_risk_applied_{stage_key}"] = round(float(risk), 2)
    trailing_key = f"calculator_trailing_high_watermark_{stage_key}"
    runtime[trailing_key] = round(max(0.0, _float_state(runtime, trailing_key, 0.0), next_pnl), 2)
    _record_pnl_trade(runtime, stage_key=stage_key, previous_pnl=previous_pnl, next_pnl=next_pnl)
    return AccountState(
        name=account.name,
        config=account.config,
        ui_state=account.ui_state,
        runtime_state=runtime,
    )


def build_default_account_config(account_kind: str) -> PropFirmConfig:
    clean_kind = account_kind.strip().lower()
    if clean_kind in {"instant", "инстант"}:
        funded = FundedConfig(
            profit_target_for_first_payout=5_000.0,
            max_loss=6_000.0,
            daily_loss=3_000.0,
            max_risk_per_trade=900.0,
            trader_split=0.8,
            drawdown_mode="trailing",
        )
        return PropFirmConfig(
            challenge_fee=350.0,
            nominal_balance=100_000.0,
            stages=[],
            funded=funded,
            prop_risk_per_trade=900.0,
            account_type="instant",
        )

    stage_count = 1 if clean_kind in {"1 фаза", "1 phase", "one phase"} else 2
    stages = [
        StageConfig(
            name="phase_1",
            profit_target=6_000.0,
            max_loss=8_000.0,
            daily_loss=4_000.0,
            max_risk_per_trade=1_900.0,
            drawdown_mode="static",
        )
    ]
    if stage_count == 2:
        stages.append(
            StageConfig(
                name="phase_2",
                profit_target=5_000.0,
                max_loss=8_000.0,
                daily_loss=4_000.0,
                max_risk_per_trade=1_500.0,
                drawdown_mode="static",
            )
        )
    funded = FundedConfig(
        profit_target_for_first_payout=5_000.0,
        max_loss=8_000.0,
        daily_loss=4_000.0,
        max_risk_per_trade=1_000.0,
        trader_split=0.8,
        drawdown_mode="static",
    )
    return PropFirmConfig(
        challenge_fee=200.0,
        nominal_balance=100_000.0,
        stages=stages,
        funded=funded,
        prop_risk_per_trade=float(stages[0].max_risk_per_trade or 1_000.0),
        account_type="challenge",
    )


def create_account_state_from_config(
    name: str,
    config: PropFirmConfig,
    *,
    ui_state: dict | None = None,
    existing_accounts: list[AccountState] | None = None,
) -> AccountState:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("account name must not be empty")
    existing_names = {account.name.strip().lower() for account in existing_accounts or []}
    if clean_name.lower() in existing_names:
        raise ValueError("account name already exists")
    return AccountState(
        name=clean_name,
        config=prop_firm_to_template_config(config),
        ui_state=dict(ui_state or {}),
        runtime_state=_initial_runtime_for_config(config),
    )


def create_account_state_from_template(
    name: str,
    template: PropTemplate,
    *,
    existing_accounts: list[AccountState] | None = None,
) -> AccountState:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("account name must not be empty")
    existing_names = {account.name.strip().lower() for account in existing_accounts or []}
    if clean_name.lower() in existing_names:
        raise ValueError("account name already exists")

    return create_account_state_from_config(
        clean_name,
        prop_firm_from_template_config(template.config),
        ui_state=template.ui_state,
        existing_accounts=existing_accounts,
    )


def rename_account_state(
    account: AccountState,
    new_name: str,
    *,
    existing_accounts: list[AccountState] | None = None,
) -> AccountState:
    clean_name = new_name.strip()
    if not clean_name:
        raise ValueError("account name must not be empty")
    current_name = account.name.strip().lower()
    for existing in existing_accounts or []:
        if existing.name.strip().lower() == clean_name.lower() and existing.name.strip().lower() != current_name:
            raise ValueError("account name already exists")
    return AccountState(
        name=clean_name,
        config=dict(account.config),
        ui_state=dict(account.ui_state),
        runtime_state=dict(account.runtime_state),
    )


def apply_template_to_account_state(account: AccountState, template: PropTemplate) -> AccountState:
    return create_account_state_from_template(account.name, template)


def apply_config_to_account_state(account: AccountState, config: PropFirmConfig, ui_state: dict | None = None) -> AccountState:
    return create_account_state_from_config(account.name, config, ui_state=ui_state if ui_state is not None else account.ui_state)


def _initial_runtime_for_config(config: PropFirmConfig) -> dict[str, object]:
    stage_options = _stage_options(config)
    first_stage_key = next(iter(stage_options))
    runtime: dict[str, object] = {
        "calculator_stage_key": first_stage_key,
        "calculator_previous_stage_key": first_stage_key,
        "calculator_current_prop_pnl": 0.0,
        "calculator_completed_personal_spent": 0.0,
        "calculator_funded_next_start_balance": 0.0,
    }
    for stage_key in stage_options:
        runtime[f"calculator_stop_points_{stage_key}"] = 100.0
        runtime[f"calculator_trade_risk_applied_{stage_key}"] = round(_stage_max_risk(config, stage_key), 2)
        runtime[f"calculator_largest_winning_trade_{stage_key}"] = 0.0
        runtime[f"calculator_trailing_high_watermark_{stage_key}"] = 0.0
        runtime[f"calculator_trade_journal_{stage_key}"] = []
    return runtime


def reset_account_runtime(account: AccountState) -> AccountState:
    config = prop_firm_from_template_config(account.config)
    first_stage_key = next(iter(_stage_options(config)))
    runtime = {
        key: value
        for key, value in account.runtime_state.items()
        if key.startswith("calculator_stop_points_") or key.startswith("calculator_trade_risk_applied_")
    }
    runtime["calculator_stage_key"] = first_stage_key
    runtime["calculator_previous_stage_key"] = first_stage_key
    runtime["calculator_current_prop_pnl"] = 0.0
    runtime["calculator_completed_personal_spent"] = 0.0
    runtime["calculator_funded_next_start_balance"] = 0.0
    for stage_key in _stage_options(config):
        runtime[f"calculator_largest_winning_trade_{stage_key}"] = 0.0
        runtime[f"calculator_trailing_high_watermark_{stage_key}"] = 0.0
        runtime[f"calculator_trade_journal_{stage_key}"] = []
    return AccountState(
        name=account.name,
        config=account.config,
        ui_state=account.ui_state,
        runtime_state=runtime,
    )


def _render_sidebar(st, accounts: list[AccountState]) -> str | None:
    st.sidebar.title("Счета")
    names = [account.name for account in accounts]
    selected = None
    if names:
        pending_selected = st.session_state.pop("cockpit_pending_selected_account", None)
        if pending_selected in names:
            st.session_state["cockpit_selected_account"] = pending_selected
        if st.session_state.get("cockpit_selected_account") not in names:
            st.session_state["cockpit_selected_account"] = names[0]
        selected_index = names.index(st.session_state["cockpit_selected_account"])
        selected = st.sidebar.selectbox(
            "Рабочий счет",
            names,
            index=selected_index,
            key="cockpit_selected_account",
        )
    else:
        st.sidebar.caption("Создай первый рабочий счет из сохраненного шаблона.")
    _render_account_creator(st, accounts)
    if selected is not None:
        _render_account_manager(st, _selected_account(accounts, str(selected)), accounts)
        _render_account_settings(st, _selected_account(accounts, str(selected)))
    st.sidebar.caption("Classic v1 сохранен отдельно. Здесь только быстрый торговый экран.")
    return str(selected) if selected is not None else None


def _render_account_creator(st, accounts: list[AccountState]) -> None:
    templates = load_prop_templates(USER_TEMPLATE_PATH)
    templates_by_name = {template.name: template for template in templates}
    with st.sidebar.expander("Новый счет", expanded=not accounts):
        source = st.radio("Источник", ["С нуля", "Из шаблона"], horizontal=True, key="cockpit_new_account_source")
        account_name = st.text_input(
            "Имя нового счета",
            value=str(st.session_state.get("cockpit_new_account_name", "")),
            placeholder="Например: ПипФарм 100к 2ф",
            key="cockpit_new_account_name",
        )
        if source == "С нуля":
            account_kind = st.selectbox("Тип", ["2 фазы", "1 фаза", "Инстант"], key="cockpit_new_account_kind")
            if st.button("Создать счет", use_container_width=True, key="cockpit_create_account"):
                try:
                    account = create_account_state_from_config(
                        account_name or account_kind,
                        build_default_account_config(account_kind),
                        ui_state=_default_ui_state_for_kind(account_kind),
                        existing_accounts=accounts,
                    )
                except ValueError as exc:
                    st.warning("Счет с таким именем уже есть." if "already exists" in str(exc) else "Напиши имя счета.")
                    return
                save_account_state(USER_ACCOUNT_STATE_PATH, account)
                st.session_state["cockpit_pending_selected_account"] = account.name
                st.rerun()
            return

        if not templates_by_name:
            st.caption("Сначала сохрани шаблон в classic-калькуляторе или создай счет с нуля.")
            return
        template_name = st.selectbox("Шаблон", list(templates_by_name), key="cockpit_new_account_template")
        if st.button("Создать счет", use_container_width=True, key="cockpit_create_account"):
            try:
                account = create_account_state_from_template(
                    account_name or template_name,
                    templates_by_name[template_name],
                    existing_accounts=accounts,
                )
            except ValueError as exc:
                st.warning("Счет с таким именем уже есть." if "already exists" in str(exc) else "Напиши имя счета.")
                return
            save_account_state(USER_ACCOUNT_STATE_PATH, account)
            st.session_state["cockpit_pending_selected_account"] = account.name
            st.rerun()


def _default_ui_state_for_kind(account_kind: str) -> dict[str, object]:
    if account_kind == "Инстант":
        return {
            "instant_consistency_enabled": False,
            "instant_consistency": 15.0,
            "minimum_profitable_days_enabled": False,
            "minimum_profitable_days_required": 5,
            "minimum_profitable_day_percent": 0.5,
            "trailing_risk_mode_label_v2": "Адаптивная",
            "execution_buffer_mode": "off",
            "execution_spread_points": 0.0,
            "execution_commission_per_lot": 0.0,
            "instant_prop_risk_strategy": "Вручную",
        }
    return {
        "phase_1_consistency_enabled": False,
        "phase_1_consistency": 35.0,
        "phase_2_consistency_enabled": False,
        "phase_2_consistency": 35.0,
        "funded_consistency_enabled": False,
        "funded_consistency": 35.0,
        "minimum_profitable_days_enabled": False,
        "minimum_profitable_days_required": 5,
        "minimum_profitable_day_percent": 0.5,
        "trailing_risk_mode_label_v2": "Консервативная",
        "execution_buffer_mode": "off",
        "execution_spread_points": 0.0,
        "execution_commission_per_lot": 0.0,
        "instant_prop_risk_strategy": "Вручную",
    }


def _render_account_manager(st, account: AccountState | None, accounts: list[AccountState]) -> None:
    if account is None:
        return
    with st.sidebar.expander("Текущий счет", expanded=False):
        new_name = st.text_input("Переименовать", value=account.name, key=f"cockpit_rename_{account.name}")
        if st.button("Сохранить имя", use_container_width=True, key=f"cockpit_rename_btn_{account.name}"):
            try:
                renamed = rename_account_state(account, new_name, existing_accounts=accounts)
            except ValueError as exc:
                st.warning("Счет с таким именем уже есть." if "already exists" in str(exc) else "Напиши имя счета.")
                return
            if renamed.name != account.name:
                delete_account_state(USER_ACCOUNT_STATE_PATH, account.name)
            save_account_state(USER_ACCOUNT_STATE_PATH, renamed)
            st.session_state["cockpit_pending_selected_account"] = renamed.name
            st.rerun()

        templates = load_prop_templates(USER_TEMPLATE_PATH)
        templates_by_name = {template.name: template for template in templates}
        if templates_by_name:
            template_name = st.selectbox(
                "Заменить настройки шаблоном",
                list(templates_by_name),
                key=f"cockpit_replace_template_{account.name}",
            )
            if st.button("Применить шаблон", use_container_width=True, key=f"cockpit_replace_btn_{account.name}"):
                updated = apply_template_to_account_state(account, templates_by_name[template_name])
                save_account_state(USER_ACCOUNT_STATE_PATH, updated)
                st.session_state["cockpit_pending_selected_account"] = updated.name
                st.rerun()
        else:
            st.caption("Нет сохраненных шаблонов для замены настроек.")

        delete_enabled = st.checkbox("Удалить этот счет", key=f"cockpit_delete_confirm_{account.name}")
        if st.button("Удалить", use_container_width=True, disabled=not delete_enabled, key=f"cockpit_delete_btn_{account.name}"):
            delete_account_state(USER_ACCOUNT_STATE_PATH, account.name)
            remaining_names = [item.name for item in accounts if item.name != account.name]
            if remaining_names:
                st.session_state["cockpit_pending_selected_account"] = remaining_names[0]
            st.rerun()


def _render_account_settings(st, account: AccountState | None) -> None:
    if account is None:
        return
    config = prop_firm_from_template_config(account.config)
    with st.sidebar.expander("Настройки счета", expanded=False):
        kind_options = ["2 фазы", "1 фаза", "Инстант"]
        current_kind = "Инстант" if _account_type(config) == "instant" else "1 фаза" if len(config.stages) == 1 else "2 фазы"
        kind = st.selectbox("Тип счета", kind_options, index=kind_options.index(current_kind), key=f"settings_kind_{account.name}")
        challenge_fee = st.number_input("Цена счета / челленджа, $", value=float(config.challenge_fee), min_value=1.0, step=10.0, key=f"settings_fee_{account.name}")
        nominal_balance = st.number_input("Размер проп-счета, $", value=float(config.nominal_balance), min_value=1.0, step=10_000.0, key=f"settings_balance_{account.name}")
        trailing_label = st.selectbox(
            "Trailing расчет риска",
            ["Адаптивная", "Консервативная", "Экономный trailing"],
            index=_trailing_label_index(account.ui_state),
            key=f"settings_trailing_{account.name}",
        )
        execution_buffer_mode = st.selectbox(
            "Execution buffer",
            ["off", "light_5", "normal_10", "safety_15"],
            index=_execution_buffer_index(account.ui_state),
            format_func={
                "off": "Без buffer",
                "light_5": "5% spread/slippage",
                "normal_10": "10% spread/slippage",
                "safety_15": "15% spread/slippage",
            }.get,
            key=f"settings_execution_buffer_{account.name}",
        )
        cost_col_1, cost_col_2 = st.columns(2)
        execution_spread_points = cost_col_1.number_input(
            "Спред/проскальз., пункты",
            value=_float_state(account.ui_state, "execution_spread_points", 0.0),
            min_value=0.0,
            step=1.0,
            key=f"settings_execution_spread_{account.name}",
        )
        execution_commission_per_lot = cost_col_2.number_input(
            "Комиссия за lot, $",
            value=_float_state(account.ui_state, "execution_commission_per_lot", 0.0),
            min_value=0.0,
            step=1.0,
            key=f"settings_execution_commission_lot_{account.name}",
        )
        st.caption("Execution buffer сам добавляется к риску как процент от стопа. Ручные поля — только дополнительная погрешность сверху.")
        instant_prop_risk_strategy = str(account.ui_state.get("instant_prop_risk_strategy", "Вручную"))
        if kind == "Инстант":
            instant_prop_risk_strategy = st.selectbox(
                "Стратегия риска пропа",
                ["Вручную", "Экономный trailing"],
                index=0 if instant_prop_risk_strategy != "Экономный trailing" else 1,
                key=f"settings_instant_prop_risk_strategy_{account.name}",
            )

        stage_settings: list[dict[str, object]] = []
        if kind != "Инстант":
            stage_count = 1 if kind == "1 фаза" else 2
            for index in range(stage_count):
                source_stage = config.stages[index] if index < len(config.stages) else build_default_account_config(kind).stages[index]
                st.markdown(f"**Этап {index + 1}**")
                stage_settings.append(
                    {
                        "name": f"phase_{index + 1}",
                        "profit_target": st.number_input(
                            f"Цель этапа {index + 1}, $",
                            value=float(source_stage.profit_target),
                            min_value=1.0,
                            step=500.0,
                            key=f"settings_stage_target_{account.name}_{index}",
                        ),
                        "max_loss": st.number_input(
                            f"Max loss этапа {index + 1}, $",
                            value=float(source_stage.max_loss),
                            min_value=1.0,
                            step=500.0,
                            key=f"settings_stage_loss_{account.name}_{index}",
                        ),
                        "daily_loss": st.number_input(
                            f"Daily loss этапа {index + 1}, $",
                            value=float(source_stage.daily_loss or 1.0),
                            min_value=1.0,
                            step=500.0,
                            key=f"settings_stage_daily_{account.name}_{index}",
                        ),
                        "max_risk_per_trade": st.number_input(
                            f"Риск этапа {index + 1}, $",
                            value=float(source_stage.max_risk_per_trade or config.prop_risk_per_trade),
                            min_value=1.0,
                            step=100.0,
                            key=f"settings_stage_risk_{account.name}_{index}",
                        ),
                        "drawdown_mode": st.selectbox(
                            f"Просадка этапа {index + 1}",
                            ["static", "trailing"],
                            index=0 if source_stage.drawdown_mode == "static" else 1,
                            key=f"settings_stage_drawdown_{account.name}_{index}",
                        ),
                    }
                )

        funded_source = config.funded
        st.markdown("**Funded / Instant**")
        funded_target = st.number_input(
            "Цель до выплаты, $",
            value=float(funded_source.profit_target_for_first_payout),
            min_value=1.0,
            step=500.0,
            key=f"settings_funded_target_{account.name}",
        )
        funded_max_loss = st.number_input("Funded max loss, $", value=float(funded_source.max_loss), min_value=1.0, step=500.0, key=f"settings_funded_loss_{account.name}")
        funded_daily_loss = st.number_input(
            "Funded daily loss, $",
            value=float(funded_source.daily_loss or 1.0),
            min_value=1.0,
            step=500.0,
            key=f"settings_funded_daily_{account.name}",
        )
        funded_risk = st.number_input(
            "Funded / instant риск, $",
            value=float(funded_source.max_risk_per_trade or config.prop_risk_per_trade),
            min_value=1.0,
            step=100.0,
            key=f"settings_funded_risk_{account.name}",
        )
        funded_split_percent = st.number_input(
            "Profit split, %",
            value=float(funded_source.trader_split * 100),
            min_value=1.0,
            max_value=100.0,
            step=5.0,
            key=f"settings_funded_split_{account.name}",
        )
        funded_drawdown = st.selectbox(
            "Funded drawdown",
            ["static", "trailing"],
            index=0 if funded_source.drawdown_mode == "static" else 1,
            key=f"settings_funded_drawdown_{account.name}",
        )

        ui_state = dict(account.ui_state)
        ui_state["trailing_risk_mode_label_v2"] = trailing_label
        ui_state["execution_buffer_mode"] = execution_buffer_mode
        ui_state["execution_spread_points"] = float(execution_spread_points)
        ui_state["execution_commission_per_lot"] = float(execution_commission_per_lot)
        ui_state["instant_prop_risk_strategy"] = instant_prop_risk_strategy
        if _execution_settings_changed(account.ui_state, ui_state):
            _save_account(
                AccountState(
                    name=account.name,
                    config=account.config,
                    ui_state=ui_state,
                    runtime_state=account.runtime_state,
                )
            )
            st.session_state["cockpit_pending_selected_account"] = account.name
            st.rerun()
        _render_rule_settings(st, ui_state, account.name, kind)

        if st.button("Сохранить настройки счета", use_container_width=True, key=f"settings_save_{account.name}"):
            stages = [
                StageConfig(
                    name=str(item["name"]),
                    profit_target=float(item["profit_target"]),
                    max_loss=float(item["max_loss"]),
                    daily_loss=float(item["daily_loss"]),
                    max_risk_per_trade=float(item["max_risk_per_trade"]),
                    drawdown_mode=str(item["drawdown_mode"]),
                )
                for item in stage_settings
            ]
            funded = FundedConfig(
                profit_target_for_first_payout=float(funded_target),
                max_loss=float(funded_max_loss),
                daily_loss=float(funded_daily_loss),
                max_risk_per_trade=float(funded_risk),
                trader_split=float(funded_split_percent) / 100,
                drawdown_mode=str(funded_drawdown),
            )
            prop_risk = float(funded_risk if kind == "Инстант" else stages[0].max_risk_per_trade or funded_risk)
            updated_config = PropFirmConfig(
                challenge_fee=float(challenge_fee),
                nominal_balance=float(nominal_balance),
                stages=[] if kind == "Инстант" else stages,
                funded=funded,
                prop_risk_per_trade=prop_risk,
                account_type="instant" if kind == "Инстант" else "challenge",
            )
            updated = apply_config_to_account_state(account, updated_config, ui_state=ui_state)
            save_account_state(USER_ACCOUNT_STATE_PATH, updated)
            st.session_state["cockpit_pending_selected_account"] = updated.name
            st.session_state[f"cockpit_reset_version_{account.name}"] = int(st.session_state.get(f"cockpit_reset_version_{account.name}", 0)) + 1
            st.rerun()


def _render_rule_settings(st, ui_state: dict, account_name: str, kind: str) -> None:
    st.markdown("**Правила**")
    consistency_keys = ["instant"] if kind == "Инстант" else ["phase_1", "phase_2", "funded"]
    if kind == "1 фаза":
        consistency_keys = ["phase_1", "funded"]
    for key in consistency_keys:
        enabled_key = f"{key}_consistency_enabled"
        percent_key = f"{key}_consistency"
        label = "Instant consistency" if key == "instant" else "Funded consistency" if key == "funded" else f"Этап {key[-1]} consistency"
        ui_state[enabled_key] = st.checkbox(label, value=bool(ui_state.get(enabled_key, False)), key=f"settings_{account_name}_{enabled_key}")
        ui_state[percent_key] = st.number_input(
            f"{label}, %",
            value=float(ui_state.get(percent_key, 35.0 if key != "instant" else 15.0)),
            min_value=1.0,
            max_value=100.0,
            step=5.0,
            key=f"settings_{account_name}_{percent_key}",
        )
    ui_state["minimum_profitable_days_enabled"] = st.checkbox(
        "Минимальные прибыльные дни",
        value=bool(ui_state.get("minimum_profitable_days_enabled", False)),
        key=f"settings_{account_name}_minimum_days_enabled",
    )
    ui_state["minimum_profitable_days_required"] = int(
        st.number_input(
            "Сколько дней",
            value=int(ui_state.get("minimum_profitable_days_required", 5)),
            min_value=1,
            step=1,
            key=f"settings_{account_name}_minimum_days_required",
        )
    )
    ui_state["minimum_profitable_day_percent"] = st.number_input(
        "Минимум за день, %",
        value=float(ui_state.get("minimum_profitable_day_percent", 0.5)),
        min_value=0.01,
        step=0.1,
        key=f"settings_{account_name}_minimum_day_percent",
    )


def _trailing_label_index(ui_state: dict) -> int:
    labels = ["Адаптивная", "Консервативная", "Экономный trailing"]
    current = str(ui_state.get("trailing_risk_mode_label_v2", "Адаптивная"))
    return labels.index(current) if current in labels else 0


def _execution_buffer_index(ui_state: dict) -> int:
    modes = ["off", "light_5", "normal_10", "safety_15"]
    current = str(ui_state.get("execution_buffer_mode", "off"))
    return modes.index(current) if current in modes else 0


def _execution_settings_changed(old_ui_state: dict, new_ui_state: dict) -> bool:
    return (
        _execution_buffer_mode(old_ui_state) != _execution_buffer_mode(new_ui_state)
        or _float_state(old_ui_state, "execution_spread_points", 0.0) != _float_state(new_ui_state, "execution_spread_points", 0.0)
        or _float_state(old_ui_state, "execution_commission_per_lot", 0.0) != _float_state(new_ui_state, "execution_commission_per_lot", 0.0)
    )


def _render_accounts_dashboard(st, pd, summaries: list[CockpitSummary]) -> None:
    with st.expander("Все активные счета", expanded=False):
        rows = [
            {
                "Счет": item.name,
                "Стадия": item.stage_label,
                "PnL": _money(item.current_pnl),
                "Риск пропа": _money(item.prop_risk),
                "Риск личного": _money(item.personal_risk),
                "Лоты": f"{item.prop_lot:.2f} / {item.hedge_lot:.2f}",
                "Маржа": "OK" if item.margin_topup <= 0 else f"+{_money(item.margin_topup)}",
            }
            for item in summaries
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_account_workbench(st, account: AccountState) -> AccountState:
    config = prop_firm_from_template_config(account.config)
    saved_summary = build_account_summary(account)
    st.markdown(f'<div class="section-label">Счет: {saved_summary.name}</div>', unsafe_allow_html=True)

    stage_options = _stage_options(config)
    widget_version = int(st.session_state.get(f"cockpit_reset_version_{account.name}", 0))
    widget_prefix = f"cockpit_{account.name}_{widget_version}"
    control_1, control_2, control_3, control_4 = st.columns([1.2, 1, 1, 1])
    stage_key = control_1.selectbox(
        "Стадия",
        list(stage_options),
        index=list(stage_options).index(saved_summary.stage_key),
        format_func=stage_options.get,
        key=f"{widget_prefix}_stage",
    )
    auto_prop_risk = _uses_auto_prop_risk(config, account.ui_state, stage_key)
    risk_default = saved_summary.prop_risk if auto_prop_risk else _float_state(account.runtime_state, f"calculator_trade_risk_applied_{stage_key}", _stage_max_risk(config, stage_key))
    risk = control_2.number_input(
        "Риск пропа",
        value=_risk_input_value(risk_default),
        min_value=1.0,
        step=100.0,
        disabled=auto_prop_risk,
        key=f"{widget_prefix}_risk_{stage_key}",
    )
    pnl = control_3.number_input(
        "Текущий PnL",
        value=saved_summary.current_pnl,
        step=_pnl_step_for_stage(config, stage_key, saved_summary.current_pnl, risk),
        help="Риск пропа задает шаг PnL. Нажимай +/- у PnL, чтобы быстро прокручивать путь по счету.",
        key=f"{widget_prefix}_pnl",
    )
    stop_default = _stop_points(account.runtime_state, stage_key)
    stop_points = control_4.number_input(
        "Стоп, пункты",
        value=stop_default,
        min_value=1.0,
        step=10.0,
        key=f"{widget_prefix}_stop_{stage_key}",
    )

    preview = preview_account_state(account, stage_key=stage_key, pnl=pnl, stop_points=stop_points, risk=risk)
    summary = build_account_summary(preview)
    if preview.runtime_state != account.runtime_state:
        _save_account(preview)

    reset_col, initial_col, prop_col = st.columns([1, 1, 1])
    if reset_col.button("Сбросить путь", use_container_width=True, key=f"cockpit_reset_{account.name}"):
        _save_reset_account(account)
        st.session_state[f"cockpit_reset_version_{account.name}"] = widget_version + 1
        st.success("Путь сброшен.")
        st.rerun()
    initial_col.markdown(_compact_info_html("Начальный личный", _money(summary.initial_personal_balance)), unsafe_allow_html=True)
    prop_col.markdown(_compact_info_html("Размер пропа", _money(summary.prop_account_size)), unsafe_allow_html=True)

    margin_label = "Маржа ок" if summary.margin_topup <= 0 else f"Докинуть {_money(summary.margin_topup)}"
    cards = [
        _risk_card_html("Риск пропа", _money(summary.prop_risk), f"<strong>{summary.prop_lot:.2f} lot</strong> · стоп {stop_points:.0f}п"),
        _risk_card_html(
            "Риск личного hedge",
            _money(summary.personal_risk),
            f"<strong>{summary.hedge_lot:.2f} lot</strong>{_execution_buffer_label(preview.ui_state)}<br>без buffer {_money(summary.base_personal_risk)}",
        ),
        _metric_card_html("Текущий PnL", _money(summary.current_pnl), f"До цели {_money(summary.distance_to_target)}"),
        _metric_card_html("Баланс личного", _money(summary.personal_balance), "после текущего PnL"),
        _metric_card_html("Осталось до max loss", _money(summary.distance_to_max_loss), ""),
        _signal_card_html("Ликвидность", margin_label, "ok" if summary.margin_topup <= 0 else "danger"),
        _signal_card_html("Consistency", summary.consistency_text, _consistency_state(preview, stage_key, summary.current_pnl)),
        _signal_card_html("Минимальные дни", summary.minimum_days_text, _minimum_days_state(summary.minimum_days_text)),
        _metric_card_html("Потрачено личных", _money(summary.personal_spent), "по текущему пути"),
    ]
    _card_grid(st, cards)
    if summary.trailing_line_text:
        st.markdown(f'<div class="info-strip">{summary.trailing_line_text}</div>', unsafe_allow_html=True)

    if stage_key in {"funded", "funded_next"}:
        _card_grid(
            st,
            [
                _metric_card_html("Профит", _money(summary.funded_profit), "funded"),
                _metric_card_html("Profit split", _money(summary.funded_split_payout), f"{config.funded.trader_split * 100:.0f}%"),
                _metric_card_html("Чистыми", _money(summary.funded_net), "минус личные затраты"),
                _metric_card_html("Чистейшие", _money(summary.funded_cleanest), "минус цена пропа"),
            ],
            columns=4,
        )
    _render_margin_panel(st, preview, summary, stop_points)
    return preview


def _save_reset_account(account: AccountState) -> None:
    _save_account(reset_account_runtime(account))


def _save_account(account: AccountState) -> None:
    save_account_state(USER_ACCOUNT_STATE_PATH, account)


def _render_margin_panel(st, account: AccountState, summary: CockpitSummary, stop_points: float) -> None:
    stage_key = summary.stage_key
    runtime = account.runtime_state
    with st.expander("Ликвидность личного hedge", expanded=False):
        col_1, col_2, col_3 = st.columns(3)
        leverage = col_1.number_input(
            "Плечо",
            value=_float_state(runtime, f"hedge_margin_leverage_{stage_key}", 300.0),
            min_value=1.0,
            step=50.0,
            key=f"margin_panel_leverage_{account.name}_{stage_key}",
        )
        stop_out = col_2.number_input(
            "Stop out, %",
            value=_float_state(runtime, f"hedge_margin_stop_out_{stage_key}", 50.0),
            min_value=1.0,
            max_value=100.0,
            step=5.0,
            key=f"margin_panel_stopout_{account.name}_{stage_key}",
        )
        extra_liquidity = col_3.number_input(
            "Неприкосновенный запас, $",
            value=_float_state(runtime, f"hedge_margin_extra_liquidity_{stage_key}", 0.0),
            min_value=0.0,
            step=50.0,
            key=f"margin_panel_extra_{account.name}_{stage_key}",
        )

        col_4, col_5, col_6 = st.columns(3)
        eurusd_price = col_4.number_input(
            "Цена EURUSD",
            value=_float_state(runtime, f"hedge_margin_eurusd_price_{stage_key}", 1.14),
            min_value=0.1,
            step=0.01,
            format="%.5f",
            key=f"margin_panel_eurusd_{account.name}_{stage_key}",
        )
        spread_points = col_5.number_input(
            "Спред, пункты",
            value=_float_state(runtime, f"hedge_margin_spread_points_{stage_key}", 0.0),
            min_value=0.0,
            step=1.0,
            key=f"margin_panel_spread_{account.name}_{stage_key}",
        )
        commission = col_6.number_input(
            "Комиссия / mio",
            value=_float_state(runtime, f"hedge_margin_commission_{stage_key}", 10.0),
            min_value=0.0,
            step=1.0,
            key=f"margin_panel_commission_{account.name}_{stage_key}",
        )

        liquidity = _hedge_margin_liquidity(
            personal_risk=summary.personal_risk,
            stop_points_5_digit=stop_points,
            leverage=float(leverage),
            eurusd_price=float(eurusd_price),
            broker_deposit=summary.personal_balance + float(extra_liquidity),
            spread_points_5_digit=float(spread_points),
            commission_per_million_per_side=float(commission),
            stop_out_percent=float(stop_out),
        )
        _card_grid(
            st,
            [
                _metric_card_html("Лот hedge", f"{liquidity['Лот hedge']:.2f}", ""),
                _metric_card_html("Маржа нужна", _money(liquidity["Маржа нужна, $"]), ""),
                _metric_card_html("Критический equity", _money(liquidity["Критический equity Stop Out, $"]), ""),
                _metric_card_html("Equity после стопа", _money(liquidity["Equity после стопа, $"]), ""),
                _metric_card_html("Запас до Stop Out", _money(liquidity["Запас до Stop Out после стопа, $"]), ""),
                _metric_card_html("Комиссия+спред", _money(liquidity["Комиссия+спред, $"]), ""),
            ],
        )
        topup = max(float(liquidity["Докинуть под маржу, $"]), float(liquidity["Докинуть чтобы стоп выдержал, $"]))
        st.markdown(
            _signal_card_html("Итог ликвидности", "Маржа ок" if topup <= 0 else f"Докинуть {_money(topup)}", "ok" if topup <= 0 else "danger"),
            unsafe_allow_html=True,
        )
        if st.button("Сохранить параметры ликвидности", use_container_width=True, key=f"margin_panel_save_{account.name}_{stage_key}"):
            updated_runtime = dict(account.runtime_state)
            margin_settings = {
                "hedge_margin_leverage": float(leverage),
                "hedge_margin_stop_out": float(stop_out),
                "hedge_margin_eurusd_price": float(eurusd_price),
                "hedge_margin_spread_points": float(spread_points),
                "hedge_margin_commission": float(commission),
                "hedge_margin_extra_liquidity": float(extra_liquidity),
            }
            for key, value in margin_settings.items():
                updated_runtime[f"{key}_{stage_key}"] = value
                updated_runtime[f"{key}_global"] = value
            _save_account(
                AccountState(
                    name=account.name,
                    config=account.config,
                    ui_state=account.ui_state,
                    runtime_state=updated_runtime,
                )
            )
            st.rerun()


def _record_pnl_trade(runtime: dict, *, stage_key: str, previous_pnl: float, next_pnl: float) -> None:
    pnl_delta = round(float(next_pnl) - float(previous_pnl), 2)
    if pnl_delta == 0:
        return
    journal_key = f"calculator_trade_journal_{stage_key}"
    journal = runtime.get(journal_key, [])
    if not isinstance(journal, list):
        journal = []
    journal.append(
        {
            "pnl_before": round(float(previous_pnl), 2),
            "pnl_after": round(float(next_pnl), 2),
            "pnl_delta": pnl_delta,
        }
    )
    runtime[journal_key] = journal[-200:]
    if pnl_delta > 0:
        largest_key = f"calculator_largest_winning_trade_{stage_key}"
        runtime[largest_key] = max(_float_state(runtime, largest_key, 0.0), pnl_delta)


def _selected_account(accounts: list[AccountState], selected_name: str | None) -> AccountState | None:
    for account in accounts:
        if account.name == selected_name:
            return account
    return None


def _initial_personal_balance(config: PropFirmConfig, prop_risk_percent: float, trailing_mode: TrailingRiskMode) -> float:
    return float(
        minimum_personal_deposit_for_strict_free_prop(
            config=config,
            prop_risk_percent=prop_risk_percent or _default_prop_risk_percent(config),
            hedge_funded=True,
            trailing_risk_mode=trailing_mode,
        ).minimum_personal_deposit
    )


def _prop_risk_for_account_strategy(
    config: PropFirmConfig,
    ui_state: dict,
    stage_key: str,
    current_pnl: float,
    manual_risk: float,
) -> float:
    if not _uses_auto_prop_risk(config, ui_state, stage_key):
        return float(manual_risk)
    recommended_risk, _reason = _economic_trailing_prop_risk(
        max_risk_per_trade=_stage_max_risk(config, stage_key),
        nominal_balance=config.nominal_balance,
        current_prop_pnl=current_pnl,
        profit_target=config.funded.profit_target_for_first_payout,
        consistency_enabled=bool(ui_state.get("instant_consistency_enabled", False)),
        consistency_percent=_float_state(ui_state, "instant_consistency", 0.0),
        minimum_days_enabled=bool(ui_state.get("minimum_profitable_days_enabled", False)),
        minimum_day_percent=_float_state(ui_state, "minimum_profitable_day_percent", 0.0),
        minimum_days_required=int(_float_state(ui_state, "minimum_profitable_days_required", 0.0)),
    )
    return round(max(1.0, float(recommended_risk)), 2)


def _uses_auto_prop_risk(config: PropFirmConfig, ui_state: dict, stage_key: str) -> bool:
    return (
        _account_type(config) == "instant"
        and stage_key in {"funded", "funded_next"}
        and str(ui_state.get("instant_prop_risk_strategy", "Вручную")) == "Экономный trailing"
        and config.funded.drawdown_mode == "trailing"
    )


def _model_stage_key(stage_key: str) -> str:
    return "funded" if stage_key in {"funded", "funded_next"} else stage_key


def _funded_next_start_balance(runtime: dict, fallback: float) -> float:
    saved = _float_state(runtime, "calculator_funded_next_start_balance", 0.0)
    return saved if saved > 0 else float(fallback)


def _funded_next_personal_risk_ratio(config: PropFirmConfig) -> float:
    return (float(config.nominal_balance) * 0.01) / max(1.0, _stage_max_loss(config, "funded"))


def _execution_buffer_mode(ui_state: dict) -> str:
    return str(ui_state.get("execution_buffer_mode", "off"))


def _execution_buffer_ratio(ui_state: dict) -> float:
    ratios = {
        "off": 0.0,
        "light_5": 0.05,
        "normal_10": 0.10,
        "safety_15": 0.15,
    }
    return ratios.get(_execution_buffer_mode(ui_state), 0.0)


def _execution_buffer_label(ui_state: dict) -> str:
    percent = _execution_buffer_ratio(ui_state) * 100
    if percent <= 0:
        return ""
    return f" · buffer {percent:g}%"


def _personal_risk_with_execution_costs(personal_risk: float, stop_points: float, ui_state: dict) -> float:
    clean_risk = max(0.0, float(personal_risk))
    hedge_lot = _lot_from_risk_and_stop_points(clean_risk, stop_points)
    auto_buffer_points = max(0.0, float(stop_points)) * _execution_buffer_ratio(ui_state)
    manual_spread_points = max(0.0, _float_state(ui_state, "execution_spread_points", 0.0))
    commission_per_lot = max(0.0, _float_state(ui_state, "execution_commission_per_lot", 0.0))
    return round(clean_risk + hedge_lot * (auto_buffer_points + manual_spread_points + commission_per_lot), 2)


def _margin_topup_for_runtime(runtime: dict, stage_key: str, personal_balance: float, personal_risk: float, stop_points: float) -> float:
    liquidity = _hedge_margin_liquidity(
        personal_risk=personal_risk,
        stop_points_5_digit=stop_points,
        leverage=_float_state(runtime, f"hedge_margin_leverage_{stage_key}", 300.0),
        eurusd_price=_float_state(runtime, f"hedge_margin_eurusd_price_{stage_key}", 1.14),
        broker_deposit=personal_balance + _float_state(runtime, f"hedge_margin_extra_liquidity_{stage_key}", 0.0),
        spread_points_5_digit=_float_state(runtime, f"hedge_margin_spread_points_{stage_key}", 0.0),
        commission_per_million_per_side=_float_state(runtime, f"hedge_margin_commission_{stage_key}", 10.0),
        stop_out_percent=_float_state(runtime, f"hedge_margin_stop_out_{stage_key}", 50.0),
    )
    return max(float(liquidity["Докинуть под маржу, $"]), float(liquidity["Докинуть чтобы стоп выдержал, $"]))


def _decision_status(config: PropFirmConfig, stage_key: str, distance_to_target: float, distance_to_max_loss: float, margin_topup: float) -> str:
    if margin_topup > 0:
        return "Нужна ликвидность"
    if distance_to_target <= 0:
        next_stage_key = _next_stage_key(_account_type(config), stage_key, _stage_options(config))
        if next_stage_key is not None:
            return _next_stage_label(next_stage_key)
        return "Цель достигнута"
    if distance_to_max_loss <= 0:
        return "Max loss"
    if distance_to_target < distance_to_max_loss * 0.25:
        return "Финиш"
    return "Рабочий режим"


def _trailing_mode_from_ui(ui_state: dict) -> TrailingRiskMode:
    label = str(ui_state.get("trailing_risk_mode_label_v2", "Адаптивная"))
    if "Консерватив" in label:
        return TrailingRiskMode.CONSERVATIVE
    if "Эконом" in label:
        return TrailingRiskMode.TARGET_LOCK
    return TrailingRiskMode.ADAPTIVE


def _consistency_text(account: AccountState, stage_key: str, current_pnl: float) -> str:
    config = prop_firm_from_template_config(account.config)
    account_type = _account_type(config)
    enabled_key, percent_key = _consistency_state_keys(account_type, stage_key)
    enabled = bool(account.ui_state.get(enabled_key, False))
    rule_percent = _float_state(account.ui_state, percent_key, 0.0)
    largest_profit = _float_state(account.runtime_state, f"calculator_largest_winning_trade_{stage_key}", 0.0)
    if largest_profit <= 0 and current_pnl > 0:
        largest_profit = float(current_pnl)
    if not enabled:
        return "—"
    if rule_percent <= 0 or largest_profit <= 0:
        return "Сделок не было"
    return f"max {_money(largest_profit)}<br>{rule_percent:g}%"


def _minimum_days_text(account: AccountState, config: PropFirmConfig, stage_key: str, current_pnl: float) -> str:
    enabled = stage_key == "funded" and bool(account.ui_state.get("minimum_profitable_days_enabled", False))
    required_days = int(_float_state(account.ui_state, "minimum_profitable_days_required", 0.0))
    minimum_day_profit = config.nominal_balance * _float_state(account.ui_state, "minimum_profitable_day_percent", 0.0) / 100
    if not enabled or required_days <= 0 or minimum_day_profit <= 0:
        return "—"
    completed_days = _profitable_days_from_journal(
        account.runtime_state.get(f"calculator_trade_journal_{stage_key}", []),
        minimum_day_profit=minimum_day_profit,
        required_days=required_days,
    )
    return f"{completed_days}/{required_days}"


def _profitable_days_from_journal(journal: object, *, minimum_day_profit: float, required_days: int) -> int:
    if required_days <= 0 or minimum_day_profit <= 0 or not isinstance(journal, list):
        return 0
    completed = 0
    for item in journal:
        if not isinstance(item, dict):
            continue
        try:
            pnl_delta = float(item.get("pnl_delta", 0.0))
        except (TypeError, ValueError):
            continue
        if pnl_delta >= minimum_day_profit:
            completed += 1
    return min(required_days, completed)


def _trailing_line_text(account: AccountState, config: PropFirmConfig, stage_key: str, current_pnl: float) -> str | None:
    if _stage_drawdown_mode(config, stage_key) != "trailing":
        return None
    high_watermark = max(
        0.0,
        _float_state(account.runtime_state, f"calculator_trailing_high_watermark_{stage_key}", max(0.0, current_pnl)),
        float(current_pnl),
    )
    trailing_max = float(config.nominal_balance) + high_watermark
    loss_line = trailing_max - _stage_max_loss(config, stage_key)
    return f"Trailing max {_money(trailing_max)} · линия слива {_money(loss_line)}"


def _stage_drawdown_mode(config: PropFirmConfig, stage_key: str) -> str:
    if stage_key.startswith("phase_"):
        index = int(stage_key.replace("phase_", "")) - 1
        return str(config.stages[index].drawdown_mode)
    return str(config.funded.drawdown_mode)


def _stage_max_loss(config: PropFirmConfig, stage_key: str) -> float:
    if stage_key.startswith("phase_"):
        index = int(stage_key.replace("phase_", "")) - 1
        return float(config.stages[index].max_loss)
    return float(config.funded.max_loss)


def _funded_payout_values(
    *,
    config: PropFirmConfig,
    stage_key: str,
    current_pnl: float,
    personal_spent: float,
) -> tuple[float, float, float, float]:
    if stage_key not in {"funded", "funded_next"}:
        return (0.0, 0.0, 0.0, 0.0)
    profit = round(max(0.0, float(current_pnl)), 2)
    split_payout = round(profit * config.funded.trader_split, 2)
    net = round(split_payout - max(0.0, float(personal_spent)), 2)
    fee_to_subtract = 0.0 if stage_key == "funded_next" else float(config.challenge_fee)
    cleanest = round(net - fee_to_subtract, 2)
    return (profit, split_payout, net, cleanest)


def _consistency_state(account: AccountState, stage_key: str, current_pnl: float) -> str:
    config = prop_firm_from_template_config(account.config)
    account_type = _account_type(config)
    enabled_key, percent_key = _consistency_state_keys(account_type, stage_key)
    enabled = bool(account.ui_state.get(enabled_key, False))
    rule_percent = _float_state(account.ui_state, percent_key, 0.0)
    largest_profit = _float_state(account.runtime_state, f"calculator_largest_winning_trade_{stage_key}", 0.0)
    if largest_profit <= 0 and current_pnl > 0:
        largest_profit = float(current_pnl)
    if not enabled:
        return "neutral"
    if rule_percent <= 0 or largest_profit <= 0:
        return "warn"
    required_profit = largest_profit / (rule_percent / 100)
    if current_pnl >= required_profit:
        return "ok"
    return "warn"


def _minimum_days_state(value: str) -> str:
    if value == "—":
        return "neutral"
    try:
        completed, required = [int(part) for part in value.split("/", 1)]
    except (ValueError, TypeError):
        return "warn"
    return "ok" if required > 0 and completed >= required else "warn"


def _cap_pnl_to_stage_target(config: PropFirmConfig, stage_key: str, pnl: float) -> float:
    target = _stage_profit_target(config, stage_key)
    pnl_value = float(pnl)
    max_loss_floor = -_stage_max_loss(config, stage_key)
    if target <= 0:
        return max(max_loss_floor, pnl_value)
    return max(max_loss_floor, min(pnl_value, target))


def _pnl_step_for_stage(config: PropFirmConfig, stage_key: str, current_pnl: float, risk: float) -> float:
    risk_step = max(1.0, _finite_amount(float(risk)))
    target = _stage_profit_target(config, stage_key)
    if target <= 0:
        return risk_step
    remaining = max(0.0, target - float(current_pnl))
    if remaining <= 0:
        return risk_step
    return max(1.0, min(risk_step, remaining))


def _risk_input_value(value: float) -> float:
    return max(1.0, _finite_amount(float(value)))


def _stage_daily_loss(config: PropFirmConfig, stage_key: str) -> float | None:
    if stage_key.startswith("phase_"):
        index = int(stage_key.replace("phase_", "")) - 1
        return config.stages[index].daily_loss
    return config.funded.daily_loss


def _stop_points(runtime: dict, stage_key: str) -> float:
    return _float_state(runtime, f"calculator_stop_points_{stage_key}", 100.0)


def _float_state(state: dict, key: str, default: float) -> float:
    try:
        value = float(state.get(key, default))
    except (TypeError, ValueError):
        return float(default)
    return value if math.isfinite(value) else float(default)


def _finite_amount(value: float) -> float:
    return float(value) if math.isfinite(float(value)) else 0.0


def _money(value: float) -> str:
    if not math.isfinite(float(value)):
        return "$0.00"
    return f"${value:,.2f}"


def _card_grid(st, cards: list[str], columns: int = 3) -> None:
    st.markdown(
        f'<div class="card-grid card-grid-{columns}">{"".join(cards)}</div>',
        unsafe_allow_html=True,
    )


def _metric_card_html(label: str, value: str, note: str) -> str:
    return (
        '<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="metric-value">{value}</div>'
        f'<div class="metric-note">{note}</div>'
        "</div>"
    )


def _risk_card_html(label: str, value: str, note: str) -> str:
    return (
        '<div class="risk-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="risk-value">{value}</div>'
        f'<div class="metric-note">{note}</div>'
        "</div>"
    )


def _signal_card_html(label: str, value: str, state: str = "neutral") -> str:
    safe_state = state if state in {"ok", "danger", "warn", "neutral"} else "neutral"
    marker = ""
    if label == "Consistency" and safe_state in {"ok", "warn"}:
        marker = '<span class="signal-marker">' + ("✓" if safe_state == "ok" else "×") + "</span>"
    return (
        f'<div class="signal-card signal-{safe_state}">'
        f'<div class="signal-label">{label}{marker}</div>'
        f'<div class="signal-value">{value}</div>'
        "</div>"
    )


def _compact_info_html(label: str, value: str) -> str:
    return (
        '<div class="compact-info">'
        f'<span>{label}</span>'
        f'<strong>{value}</strong>'
        "</div>"
    )


def _metric_card(container, label: str, value: str, note: str) -> None:
    container.markdown(_metric_card_html(label, value, note), unsafe_allow_html=True)


def _risk_card(container, label: str, value: str, note: str) -> None:
    container.markdown(_risk_card_html(label, value, note), unsafe_allow_html=True)


def _signal_card(container, label: str, value: str, state: str = "neutral") -> None:
    container.markdown(_signal_card_html(label, value, state), unsafe_allow_html=True)


def _inject_cockpit_css(st) -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.35rem; max-width: 1320px; }
        .cockpit-title { font-size: 34px; font-weight: 750; color: #202532; letter-spacing: 0; margin-bottom: 0; }
        .cockpit-subtitle { color: #6b7280; font-size: 14px; margin-bottom: 14px; }
        .section-label { font-size: 18px; font-weight: 700; color: #202532; margin: 14px 0 8px; }
        .card-grid {
            display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 18px 22px; margin-top: 18px; align-items: stretch;
        }
        .card-grid-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
        .card-grid > div { height: 100%; box-sizing: border-box; }
        .metric-card {
            min-height: 130px; border: 1px solid #e6e8ee; border-radius: 8px; padding: 18px 20px;
            background: #ffffff; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .risk-card {
            min-height: 130px; border: 1px solid #d7e3f3; border-radius: 8px; padding: 18px 20px;
            background: #f8fbff; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .metric-label { color: #69707f; font-size: 13px; margin-bottom: 7px; }
        .metric-value { color: #252a36; font-size: 29px; line-height: 1.08; font-weight: 650; overflow-wrap: anywhere; }
        .risk-value { color: #172033; font-size: 40px; line-height: 1; font-weight: 780; overflow-wrap: anywhere; }
        .metric-note { color: #8a91a0; font-size: 13px; margin-top: 9px; min-height: 16px; }
        .metric-note strong { color: #252a36; font-weight: 800; }
        .compact-info {
            min-height: 48px; border: 1px solid #e6e8ee; border-radius: 8px; padding: 8px 14px;
            display: flex; align-items: center; justify-content: space-between; gap: 12px; background: #ffffff;
        }
        .compact-info span { color: #7b8290; font-size: 13px; }
        .compact-info strong { color: #252a36; font-size: 16px; font-weight: 760; white-space: nowrap; }
        .info-strip {
            margin-top: 16px; border-radius: 8px; padding: 14px 18px; background: #e8f2ff;
            border: 1px solid #cfe2fb; color: #0057ad; font-size: 16px; font-weight: 650;
        }
        .signal-card {
            min-height: 130px; border-radius: 8px; padding: 18px 20px;
            border: 1px solid #e0e7f1; background: #ffffff; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .signal-label { color: #69707f; font-size: 13px; margin-bottom: 9px; display: flex; align-items: center; gap: 8px; }
        .signal-marker {
            width: 20px; height: 20px; border-radius: 999px; display: inline-flex; align-items: center; justify-content: center;
            font-size: 15px; font-weight: 850; line-height: 1; background: rgba(255,255,255,0.7);
        }
        .signal-value { color: #252a36; font-size: 24px; line-height: 1.15; font-weight: 780; overflow-wrap: anywhere; }
        .signal-ok { background: #eaf8ef; border-color: #bee8cc; }
        .signal-ok .signal-value { color: #087a34; }
        .signal-danger { background: #ffecec; border-color: #ffc8c8; }
        .signal-danger .signal-value { color: #b42318; }
        .signal-warn { background: #fff7df; border-color: #f5df9f; }
        .signal-warn .signal-value { color: #936400; }
        .empty-panel {
            border: 1px solid #e6e8ee; border-radius: 8px; padding: 24px; color: #69707f; background: #fff;
        }
        div[data-testid="stDataFrame"] { border: 1px solid #e6e8ee; border-radius: 8px; overflow: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
