from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any

from prop_research.domain.config import FundedConfig, PropFirmConfig, StageConfig


@dataclass(frozen=True)
class PropTemplate:
    name: str
    config: dict[str, Any]
    ui_state: dict[str, Any]


def load_prop_templates(path: str | Path) -> list[PropTemplate]:
    template_path = Path(path)
    if not template_path.exists():
        return []
    raw = json.loads(template_path.read_text(encoding="utf-8"))
    return [
        PropTemplate(
            name=str(item["name"]),
            config=dict(item["config"]),
            ui_state=dict(item.get("ui_state", {})),
        )
        for item in raw.get("templates", [])
        if item.get("name") and item.get("config")
    ]


def save_prop_template(
    path: str | Path,
    *,
    name: str,
    config: PropFirmConfig,
    ui_state: dict[str, Any],
) -> None:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("template name must not be empty")
    templates = [template for template in load_prop_templates(path) if template.name != clean_name]
    templates.append(
        PropTemplate(
            name=clean_name,
            config=prop_firm_to_template_config(config),
            ui_state=dict(ui_state),
        )
    )
    _write_templates(path, templates)


def delete_prop_template(path: str | Path, name: str) -> None:
    templates = [template for template in load_prop_templates(path) if template.name != name]
    _write_templates(path, templates)


def prop_firm_to_template_config(config: PropFirmConfig) -> dict[str, Any]:
    return asdict(config)


def prop_firm_from_template_config(raw: dict[str, Any]) -> PropFirmConfig:
    return PropFirmConfig(
        challenge_fee=float(raw["challenge_fee"]),
        nominal_balance=float(raw["nominal_balance"]),
        prop_risk_per_trade=float(raw["prop_risk_per_trade"]),
        stages=[
            StageConfig(
                name=str(stage["name"]),
                profit_target=float(stage["profit_target"]),
                max_loss=float(stage["max_loss"]),
                max_loss_mode=str(stage.get("max_loss_mode", "amount")),
                daily_loss=float(stage["daily_loss"]) if stage.get("daily_loss") is not None else None,
                daily_loss_mode=str(stage.get("daily_loss_mode", "amount")),
                max_risk_per_trade=float(stage["max_risk_per_trade"])
                if stage.get("max_risk_per_trade") is not None
                else None,
                drawdown_mode=str(stage.get("drawdown_mode", "static")),
            )
            for stage in raw.get("stages", [])
        ],
        funded=FundedConfig(
            profit_target_for_first_payout=float(raw["funded"]["profit_target_for_first_payout"]),
            max_loss=float(raw["funded"]["max_loss"]),
            trader_split=float(raw["funded"]["trader_split"]),
            max_loss_mode=str(raw["funded"].get("max_loss_mode", "amount")),
            daily_loss=float(raw["funded"]["daily_loss"]) if raw["funded"].get("daily_loss") is not None else None,
            daily_loss_mode=str(raw["funded"].get("daily_loss_mode", "amount")),
            max_risk_per_trade=float(raw["funded"]["max_risk_per_trade"])
            if raw["funded"].get("max_risk_per_trade") is not None
            else None,
            drawdown_mode=str(raw["funded"].get("drawdown_mode", "static")),
        ),
        account_type=str(raw.get("account_type", "challenge")),
    )


def _write_templates(path: str | Path, templates: list[PropTemplate]) -> None:
    template_path = Path(path)
    template_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "templates": [
            {
                "name": template.name,
                "config": template.config,
                "ui_state": template.ui_state,
            }
            for template in sorted(templates, key=lambda item: item.name.lower())
        ],
    }
    template_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
