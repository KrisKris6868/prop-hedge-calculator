from prop_research.config.account_states import (
    AccountState,
    delete_account_state,
    load_account_states,
    save_account_state,
)


def test_save_load_and_overwrite_account_state(tmp_path) -> None:
    path = tmp_path / "account_states.json"
    save_account_state(
        path,
        AccountState(
            name="PipFarm 100k июль",
            config={"nominal_balance": 100_000, "account_type": "challenge"},
            ui_state={"funded_consistency": 35.0},
            runtime_state={"calculator_current_prop_pnl": 1_900.0},
        ),
    )
    save_account_state(
        path,
        AccountState(
            name="PipFarm 100k июль",
            config={"nominal_balance": 100_000, "account_type": "challenge"},
            ui_state={"funded_consistency": 35.0},
            runtime_state={"calculator_current_prop_pnl": 3_800.0},
        ),
    )

    accounts = load_account_states(path)

    assert len(accounts) == 1
    assert accounts[0].name == "PipFarm 100k июль"
    assert accounts[0].runtime_state == {"calculator_current_prop_pnl": 3_800.0}


def test_delete_account_state_removes_only_selected_account(tmp_path) -> None:
    path = tmp_path / "account_states.json"
    save_account_state(path, AccountState(name="A", config={}, ui_state={}, runtime_state={}))
    save_account_state(path, AccountState(name="B", config={}, ui_state={}, runtime_state={}))

    delete_account_state(path, "A")

    assert [account.name for account in load_account_states(path)] == ["B"]


def test_load_account_states_ignores_missing_file(tmp_path) -> None:
    assert load_account_states(tmp_path / "missing.json") == []
