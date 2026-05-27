import pytest


def _seed_with_memory(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-m1", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-p1", "name": "Costco", "transfer_account_id": None},
    )
    store.set_merchant_memory(
        store.merchant_by_alias("Costco")["id"],
        categories_used={"cat-groc": 3, "cat-house": 1},
        processings={
            "-8421": {"parent_memo": "weekly", "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}]},
            "-1500": {"parent_memo": "snack", "splits": [{"category_id": "cat-house", "amount_milliunits": -1500, "memo": ""}]},
        },
    )
    return store


@pytest.mark.asyncio
async def test_memory_screen_lists_merchants(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        await pilot.pause()
        # 1 header + 2 entries = 3 rows.
        assert screen.row_count() == 3


@pytest.mark.asyncio
async def test_memory_screen_delete_entry(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        await pilot.pause()
        merchant_id = store.merchant_by_alias("Costco")["id"]
        screen.delete_entry(merchant_id, "-8421")
        await pilot.pause()
        m = store.merchant_by_alias("Costco")
        assert "-8421" not in m["processings"]
        assert "-1500" in m["processings"]


@pytest.mark.asyncio
async def test_memory_screen_reset_merchant(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        merchant_id = store.merchant_by_alias("Costco")["id"]
        screen.reset_merchant(merchant_id)
        await pilot.pause()
        m = store.merchant_by_alias("Costco")
        assert m["processings"] == {}
        assert m["categories_used"] == {}
