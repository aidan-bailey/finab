import pytest


def _seed_store_with_merchants(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-m1", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-p1", "name": "Costco", "transfer_account_id": None},
    )
    store.add_merchant(
        alias="Self → Savings",
        fw_record={"id": "fw-m2", "name": "Self Transfer", "samples": []},
        ynab_record={"id": "yn-p2", "name": "Transfer: Savings", "transfer_account_id": "yn-sav"},
    )
    return store


@pytest.mark.asyncio
async def test_merchants_screen_lists_merchants(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        screen = app.query_one(MerchantsScreen)
        screen.refresh_rows()
        await pilot.pause()
        assert screen.row_count() == 2


@pytest.mark.asyncio
async def test_merchants_screen_rename(tmp_path):
    """action_rename opens AliasInputModal; on success, store is updated."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        screen = app.query_one(MerchantsScreen)
        screen.refresh_rows()
        screen.set_cursor(0)
        await pilot.pause()
        screen.action_rename()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        # Find the Input widget and set value directly (more reliable than typing
        # one char at a time, which can race with input updates).
        from textual.widgets import Input
        inp = app.screen.query_one(Input)
        inp.value = "Costco Wholesale"
        await pilot.pause()
        # Use action_submit directly — pilot.press("enter") is intercepted by
        # the app-level priority binding (action_sync_repeat_closest) before it
        # reaches the focused Input.
        await inp.action_submit()
        await pilot.pause()
        assert store.merchant_by_alias("Costco Wholesale") is not None
