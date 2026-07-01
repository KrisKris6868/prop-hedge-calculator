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
    _default_prop_risk_percent,
    _hedge_margin_liquidity,
    _lot_from_risk_and_stop_points,
    _risk_percent_from_amount,
    _stage_drawdown_mode,
    _stage_max_loss,
    _stage_max_risk,
    _stage_options,
    _stage_profit_target,
)
from prop_research.config.account_states import AccountState, load_account_states, save_account_state
from prop_research.config.templates import prop_firm_from_template_config, prop_firm_to_template_config
from prop_research.domain.config import PropFirmConfig

USER_ACCOUNT_STATE_PATH = PROJECT_ROOT / ".streamlit" / "account_states.json"


@dataclass(frozen=True)
class CockpitSummary:
    name: str
    account_type: str
    stage_key: str
    stage_label: str
    current_pnl: float
    prop_risk: float
    personal_risk: float
    prop_lot: float
    hedge_lot: float
    personal_balance: float
    distance_to_target: float
    distance_to_max_loss: float
    margin_topup: float
    status: str


def main() -> None:
    import pandas as pd
    import streamlit as st

    st.set_page_config(page_title="Trading Cockpit", layout="wide")
    _inject_cockpit_css(st)

    accounts = load_account_states(USER_ACCOUNT_STATE_PATH)
    summaries = [build_account_summary(account) for account in accounts]
    selected_name = _render_sidebar(st, accounts)

    st.markdown('<div class="cockpit-title">Trading Cockpit</div>', unsafe_allow_html=True)
    st.markdown('<div class="cockpit-subtitle">Счета, риск, лотность и маржа в одном рабочем экране.</div>', unsafe_allow_html=True)

    if not accounts:
        st.markdown('<div class="empty-panel">Нет сохраненных рабочих счетов. Создай путь в classic-калькуляторе и вернись сюда.</div>', unsafe_allow_html=True)
        return

    _render_accounts_dashboard(st, pd, summaries)
    selected_account = _selected_account(accounts, selected_name)
    if selected_account is None:
        selected_account = accounts[0]
    _render_account_workbench(st, selected_account)


def build_account_summary(account: AccountState) -> CockpitSummary:
    config = prop_firm_from_template_config(account.config)
    runtime = account.runtime_state
    ui_state = account.ui_state
    stage_options = _stage_options(config)
    stage_key = str(runtime.get("calculator_stage_key") or next(iter(stage_options)))
    if stage_key not in stage_options:
        stage_key = next(iter(stage_options))
    current_pnl = _float_state(runtime, "calculator_current_prop_pnl", 0.0)
    stop_points = _float_state(runtime, f"calculator_stop_points_{stage_key}", 100.0)
    prop_risk = _float_state(runtime, f"calculator_trade_risk_applied_{stage_key}", _stage_max_risk(config, stage_key))
    prop_risk_percent = _risk_percent_from_amount(prop_risk, config.nominal_balance)
    trailing_mode = _trailing_mode_from_ui(ui_state)
    initial_personal_balance = _initial_personal_balance(config, prop_risk_percent, trailing_mode)
    personal_balance_state = calculate_personal_balance_from_prop_pnl(
        config=config,
        stage_key=stage_key,
        current_prop_pnl=current_pnl,
        initial_personal_balance=initial_personal_balance,
        prop_risk_percent=prop_risk_percent,
        mode=CoverageMode.GROW_DEPOSIT_BY_FEE,
        trailing_risk_mode=trailing_mode,
        trailing_high_watermark=_float_state(runtime, f"calculator_trailing_high_watermark_{stage_key}", max(0.0, current_pnl)),
    )
    personal_balance = float(personal_balance_state["Текущий баланс личного счета, $"])
    trade = calculate_personal_risk_for_trade(
        config=config,
        stage_key=stage_key,
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
    personal_risk = float(trade["Риск личного, $"])
    prop_lot = _lot_from_risk_and_stop_points(effective_prop_risk, stop_points)
    hedge_lot = _lot_from_risk_and_stop_points(personal_risk, stop_points)
    margin_topup = _margin_topup_for_runtime(runtime, stage_key, personal_balance, personal_risk, stop_points)
    distance_to_target = float(trade["distance_to_target"])
    distance_to_max_loss = float(trade["distance_to_max_loss"])
    return CockpitSummary(
        name=account.name,
        account_type=_account_type(config),
        stage_key=stage_key,
        stage_label=stage_options[stage_key],
        current_pnl=current_pnl,
        prop_risk=effective_prop_risk,
        personal_risk=personal_risk,
        prop_lot=prop_lot,
        hedge_lot=hedge_lot,
        personal_balance=personal_balance,
        distance_to_target=distance_to_target,
        distance_to_max_loss=distance_to_max_loss,
        margin_topup=margin_topup,
        status=_decision_status(distance_to_target, distance_to_max_loss, margin_topup),
    )


def _render_sidebar(st, accounts: list[AccountState]) -> str | None:
    st.sidebar.title("Счета")
    names = [account.name for account in accounts]
    if not names:
        return None
    selected = st.sidebar.selectbox("Рабочий счет", names, key="cockpit_selected_account")
    st.sidebar.caption("Classic v1 сохранен отдельно. Здесь только быстрый торговый экран.")
    return str(selected)


def _render_accounts_dashboard(st, pd, summaries: list[CockpitSummary]) -> None:
    st.markdown('<div class="section-label">Все активные счета</div>', unsafe_allow_html=True)
    rows = [
        {
            "Счет": item.name,
            "Стадия": item.stage_label,
            "PnL": _money(item.current_pnl),
            "Риск пропа": _money(item.prop_risk),
            "Prop lot": f"{item.prop_lot:.2f}",
            "Hedge lot": f"{item.hedge_lot:.2f}",
            "Маржа": "OK" if item.margin_topup <= 0 else f"+{_money(item.margin_topup)}",
            "Статус": item.status,
        }
        for item in summaries
    ]
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _render_account_workbench(st, account: AccountState) -> None:
    config = prop_firm_from_template_config(account.config)
    summary = build_account_summary(account)
    st.markdown(f'<div class="section-label">Сейчас торгуем: {summary.name}</div>', unsafe_allow_html=True)
    top_1, top_2, top_3, top_4 = st.columns(4)
    _metric_card(top_1, "Стадия", summary.stage_label, summary.status)
    _metric_card(top_2, "Текущий PnL", _money(summary.current_pnl), f"До цели {_money(summary.distance_to_target)}")
    _metric_card(top_3, "Prop lot", f"{summary.prop_lot:.2f}", f"Риск {_money(summary.prop_risk)}")
    _metric_card(top_4, "Hedge lot", f"{summary.hedge_lot:.2f}", f"Личный риск {_money(summary.personal_risk)}")

    signal_1, signal_2, signal_3 = st.columns([1.2, 1, 1])
    signal_1.markdown(
        f"""
        <div class="decision-panel">
          <div class="decision-label">Решение перед входом</div>
          <div class="decision-main">Prop {summary.prop_lot:.2f} lot / Hedge {summary.hedge_lot:.2f} lot</div>
          <div class="decision-note">Стоп из счета: {_stop_points(account.runtime_state, summary.stage_key):.0f} пунктов</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    _metric_card(signal_2, "Баланс личного", _money(summary.personal_balance), "после текущего PnL")
    margin_label = "Маржа ок" if summary.margin_topup <= 0 else f"Докинуть {_money(summary.margin_topup)}"
    _metric_card(signal_3, "Ликвидность", margin_label, f"До max loss {_money(summary.distance_to_max_loss)}")

    with st.form(f"cockpit_update_{account.name}"):
        stage_options = _stage_options(config)
        input_1, input_2, input_3, input_4 = st.columns(4)
        stage_key = input_1.selectbox(
            "Стадия",
            list(stage_options),
            index=list(stage_options).index(summary.stage_key),
            format_func=stage_options.get,
        )
        pnl = input_2.number_input("Текущий PnL", value=summary.current_pnl, step=max(1.0, summary.prop_risk))
        stop_points = input_3.number_input("Стоп, пункты", value=_stop_points(account.runtime_state, summary.stage_key), min_value=1.0, step=10.0)
        risk = input_4.number_input("Риск пропа", value=summary.prop_risk, min_value=1.0, step=100.0)
        submitted = st.form_submit_button("Обновить счет", use_container_width=True)
    if submitted:
        _save_cockpit_update(account, config, stage_key, pnl, stop_points, risk)
        st.success("Счет обновлен.")
        st.rerun()

    details_1, details_2, details_3, details_4 = st.columns(4)
    _metric_card(details_1, "Осталось до цели", _money(summary.distance_to_target), "")
    _metric_card(details_2, "Осталось до max loss", _money(summary.distance_to_max_loss), "")
    _metric_card(details_3, "Риск личного", _money(summary.personal_risk), f"{summary.hedge_lot:.2f} lot")
    _metric_card(details_4, "Тип счета", "Instant" if summary.account_type == "instant" else "Challenge", summary.stage_key)


def _save_cockpit_update(account: AccountState, config: PropFirmConfig, stage_key: str, pnl: float, stop_points: float, risk: float) -> None:
    runtime = dict(account.runtime_state)
    runtime["calculator_stage_key"] = stage_key
    runtime["calculator_current_prop_pnl"] = round(float(pnl), 2)
    runtime[f"calculator_stop_points_{stage_key}"] = round(float(stop_points), 2)
    runtime[f"calculator_trade_risk_applied_{stage_key}"] = round(float(risk), 2)
    if _stage_drawdown_mode(config, stage_key) == "trailing":
        trailing_key = f"calculator_trailing_high_watermark_{stage_key}"
        runtime[trailing_key] = max(float(runtime.get(trailing_key, 0.0)), float(pnl), 0.0)
    save_account_state(
        USER_ACCOUNT_STATE_PATH,
        AccountState(
            name=account.name,
            config=account.config,
            ui_state=account.ui_state,
            runtime_state=runtime,
        ),
    )


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


def _decision_status(distance_to_target: float, distance_to_max_loss: float, margin_topup: float) -> str:
    if margin_topup > 0:
        return "Нужна ликвидность"
    if distance_to_target <= 0:
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


def _money(value: float) -> str:
    if not math.isfinite(float(value)):
        return "недоступно"
    return f"${value:,.2f}"


def _metric_card(container, label: str, value: str, note: str) -> None:
    container.markdown(
        f"""
        <div class="metric-card">
          <div class="metric-label">{label}</div>
          <div class="metric-value">{value}</div>
          <div class="metric-note">{note}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _inject_cockpit_css(st) -> None:
    st.markdown(
        """
        <style>
        .block-container { padding-top: 2rem; max-width: 1280px; }
        .cockpit-title { font-size: 38px; font-weight: 750; color: #202532; letter-spacing: 0; margin-bottom: 2px; }
        .cockpit-subtitle { color: #6b7280; font-size: 15px; margin-bottom: 22px; }
        .section-label { font-size: 18px; font-weight: 700; color: #202532; margin: 22px 0 10px; }
        .metric-card {
            min-height: 118px; border: 1px solid #e6e8ee; border-radius: 8px; padding: 16px 18px;
            background: #ffffff; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .metric-label { color: #69707f; font-size: 14px; margin-bottom: 8px; }
        .metric-value { color: #252a36; font-size: 32px; line-height: 1.08; font-weight: 650; overflow-wrap: anywhere; }
        .metric-note { color: #8a91a0; font-size: 13px; margin-top: 10px; min-height: 18px; }
        .decision-panel {
            min-height: 154px; border-radius: 8px; padding: 20px 22px; background: #eaf3ff;
            border: 1px solid #cfe3ff; color: #0f4f9c;
        }
        .decision-label { font-size: 14px; color: #3571b8; margin-bottom: 10px; }
        .decision-main { font-size: 30px; line-height: 1.12; font-weight: 760; color: #074a91; }
        .decision-note { font-size: 14px; margin-top: 14px; color: #3d73a9; }
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
