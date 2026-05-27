import pytest


def _seed_store(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase Checking",
        fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpa"},
        ignore_transactions=False,
    )
    store.add_account(
        alias="Crypto Wallet",
        fw_record={"id": "fw-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "transfer_payee_id": "yn-tpb"},
        ignore_transactions=True,
    )
    return store


@pytest.mark.asyncio
async def test_accounts_screen_lists_accounts(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        await pilot.pause()
        assert ac_screen.row_count() == 2


@pytest.mark.asyncio
async def test_accounts_screen_toggle_ignore(tmp_path):
    """Calling action_toggle_ignore on the screen flips the store entry."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        ac_screen.set_cursor(0)
        await pilot.pause()
        ac_screen.action_toggle_ignore()
        await pilot.pause()
        acc = store.account_by_finwise_id("fw-a")
        assert acc["ignore_transactions"] is True
