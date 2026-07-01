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
    _hedge_margin_liquidity,
    _lot_from_risk_and_stop_points,
    _next_stage_key,
    _next_stage_label,
    _profitable_days_from_pnl,
    _risk_percent_from_amount,
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
    consistency_text: str
    minimum_days_text: str
    personal_spent: float
    funded_profit: float
    funded_split_payout: float
    funded_net: float
    funded_cleanest: float


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
        st.markdown('<div class="empty-panel">Нет сохраненных рабочих счетов. Создай путь в classic-калькуляторе и вернись сюда.</div>', unsafe_allow_html=True)
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
    current_stage_spent = max(0.0, initial_personal_balance - personal_balance)
    completed_spent = _float_state(runtime, "calculator_completed_personal_spent", 0.0) if _account_type(config) == "challenge" else 0.0
    personal_spent = round(completed_spent + current_stage_spent, 2)
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
    personal_risk = _finite_amount(float(trade["Риск личного, $"]))
    prop_lot = _lot_from_risk_and_stop_points(effective_prop_risk, stop_points)
    hedge_lot = _lot_from_risk_and_stop_points(personal_risk, stop_points)
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
        funded_profit=funded_profit,
        funded_split_payout=funded_split_payout,
        funded_net=funded_net,
        funded_cleanest=funded_cleanest,
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
    _record_pnl_trade(runtime, stage_key=stage_key, previous_pnl=previous_pnl, next_pnl=next_pnl)
    return AccountState(
        name=account.name,
        config=account.config,
        ui_state=account.ui_state,
        runtime_state=runtime,
    )


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
    if not names:
        return None
    selected = st.sidebar.selectbox("Рабочий счет", names, key="cockpit_selected_account")
    st.sidebar.caption("Classic v1 сохранен отдельно. Здесь только быстрый торговый экран.")
    return str(selected)


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
    risk_default = _float_state(account.runtime_state, f"calculator_trade_risk_applied_{stage_key}", _stage_max_risk(config, stage_key))
    risk = control_2.number_input(
        "Риск пропа",
        value=risk_default,
        min_value=1.0,
        step=100.0,
        key=f"{widget_prefix}_risk_{stage_key}",
    )
    pnl = control_3.number_input(
        "Текущий PnL",
        value=saved_summary.current_pnl,
        step=_pnl_step_for_stage(config, stage_key, saved_summary.current_pnl, risk),
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

    reset_col, spacer_col = st.columns([1, 3])
    if reset_col.button("Сбросить путь", use_container_width=True, key=f"cockpit_reset_{account.name}"):
        _save_reset_account(account)
        st.session_state[f"cockpit_reset_version_{account.name}"] = widget_version + 1
        st.success("Путь сброшен.")
        st.rerun()
    spacer_col.caption("Риск пропа задает шаг PnL. Нажимай +/- у PnL, чтобы быстро прокручивать путь по счету.")

    margin_label = "Маржа ок" if summary.margin_topup <= 0 else f"Докинуть {_money(summary.margin_topup)}"
    cards = [
        _risk_card_html("Риск пропа", _money(summary.prop_risk), f"{summary.prop_lot:.2f} lot · стоп {stop_points:.0f}п"),
        _risk_card_html("Риск личного hedge", _money(summary.personal_risk), f"{summary.hedge_lot:.2f} lot"),
        _metric_card_html("Текущий PnL", _money(summary.current_pnl), f"До цели {_money(summary.distance_to_target)}"),
        _metric_card_html("Баланс личного", _money(summary.personal_balance), "после текущего PnL"),
        _metric_card_html("Осталось до max loss", _money(summary.distance_to_max_loss), ""),
        _signal_card_html("Ликвидность", margin_label, "ok" if summary.margin_topup <= 0 else "danger"),
        _signal_card_html("Consistency", summary.consistency_text, _consistency_state(summary.consistency_text)),
        _signal_card_html("Минимальные дни", summary.minimum_days_text, _minimum_days_state(summary.minimum_days_text)),
        _metric_card_html("Потрачено личных", _money(summary.personal_spent), "по текущему пути"),
    ]
    _card_grid(st, cards)

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
    return preview


def _save_reset_account(account: AccountState) -> None:
    _save_account(reset_account_runtime(account))


def _save_account(account: AccountState) -> None:
    save_account_state(USER_ACCOUNT_STATE_PATH, account)


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
    required_profit = largest_profit / (rule_percent / 100)
    marker = "✓" if current_pnl >= required_profit else "✕"
    return f"max {_money(largest_profit)} · {rule_percent:g}% {marker}"


def _minimum_days_text(account: AccountState, config: PropFirmConfig, stage_key: str, current_pnl: float) -> str:
    enabled = stage_key == "funded" and bool(account.ui_state.get("minimum_profitable_days_enabled", False))
    required_days = int(_float_state(account.ui_state, "minimum_profitable_days_required", 0.0))
    minimum_day_profit = config.nominal_balance * _float_state(account.ui_state, "minimum_profitable_day_percent", 0.0) / 100
    if not enabled or required_days <= 0 or minimum_day_profit <= 0:
        return "—"
    completed_days = _profitable_days_from_pnl(
        current_prop_pnl=current_pnl,
        minimum_day_profit=minimum_day_profit,
        required_days=required_days,
    )
    return f"{completed_days}/{required_days}"


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


def _consistency_state(value: str) -> str:
    if value == "—":
        return "neutral"
    if "✓" in value:
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
    if target <= 0:
        return pnl_value
    return min(pnl_value, target)


def _pnl_step_for_stage(config: PropFirmConfig, stage_key: str, current_pnl: float, risk: float) -> float:
    risk_step = max(1.0, _finite_amount(float(risk)))
    target = _stage_profit_target(config, stage_key)
    if target <= 0:
        return risk_step
    remaining = max(0.0, target - float(current_pnl))
    if remaining <= 0:
        return risk_step
    return max(1.0, min(risk_step, remaining))


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
    return (
        f'<div class="signal-card signal-{safe_state}">'
        f'<div class="signal-label">{label}</div>'
        f'<div class="signal-value">{value}</div>'
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
        .signal-card {
            min-height: 130px; border-radius: 8px; padding: 18px 20px;
            border: 1px solid #e0e7f1; background: #ffffff; box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .signal-label { color: #69707f; font-size: 13px; margin-bottom: 9px; }
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
