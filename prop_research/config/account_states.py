from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AccountState:
    name: str
    config: dict[str, Any]
    ui_state: dict[str, Any]
    runtime_state: dict[str, Any]


def load_account_states(path: str | Path) -> list[AccountState]:
    state_path = Path(path)
    if not state_path.exists():
        return []
    raw = json.loads(state_path.read_text(encoding="utf-8"))
    return [
        AccountState(
            name=str(item["name"]),
            config=dict(item.get("config", {})),
            ui_state=dict(item.get("ui_state", {})),
            runtime_state=dict(item.get("runtime_state", {})),
        )
        for item in raw.get("accounts", [])
        if item.get("name")
    ]


def save_account_state(path: str | Path, account: AccountState) -> None:
    clean_name = account.name.strip()
    if not clean_name:
        raise ValueError("account name must not be empty")
    accounts = [item for item in load_account_states(path) if item.name != clean_name]
    accounts.append(
        AccountState(
            name=clean_name,
            config=dict(account.config),
            ui_state=dict(account.ui_state),
            runtime_state=dict(account.runtime_state),
        )
    )
    _write_account_states(path, accounts)


def delete_account_state(path: str | Path, name: str) -> None:
    accounts = [item for item in load_account_states(path) if item.name != name]
    _write_account_states(path, accounts)


def _write_account_states(path: str | Path, accounts: list[AccountState]) -> None:
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "accounts": [
            {
                "name": account.name,
                "config": account.config,
                "ui_state": account.ui_state,
                "runtime_state": account.runtime_state,
            }
            for account in sorted(accounts, key=lambda item: item.name.lower())
        ],
    }
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
