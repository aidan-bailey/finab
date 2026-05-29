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


class _FakeFwTxn:
    """Stub matching the Transaction shape that _extract_distinct_merchants reads."""
    def __init__(self, merchant_id, merchant_name=None, memo=None, amount=-1000):
        self.merchant_id = merchant_id
        self.merchant_name = merchant_name
        self.memo = memo
        self.original_description = None
        self.payee_name = None
        self.amount = amount
        from datetime import date as date_cls
        self.date = date_cls.today()


class _StubYnabClientForMerchants:
    def __init__(self):
        self.created_payees = []
    def create_payee(self, budget_id, name):
        class _P:
            pass
        p = _P()
        p.id = f"yn-new-payee-{len(self.created_payees)}"
        p.name = name
        p.transfer_account_id = None
        self.created_payees.append(p)
        return p


@pytest.mark.asyncio
async def test_merchants_screen_shows_unmapped(tmp_path):
    """Distinct merchant_ids from fw_transactions that aren't in the
    store render as unmapped rows."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)  # has fw-m1 (Costco) and fw-m2
    txns = [
        _FakeFwTxn(merchant_id="fw-m1"),   # mapped
        _FakeFwTxn(merchant_id="fw-new-x", merchant_name="New Merchant"),  # unmapped
    ]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        ms = app.query_one(MerchantsScreen)
        ms.bind_data(
            store=store,
            fw_transactions=txns,
            ynab_payees=[],
            ynab_client=None,
            budget_id=None,
        )
        await pilot.pause()
        # 2 mapped + 1 unmapped = 3 rows.
        assert ms.row_count() == 3
        assert ms.has_unmapped_for("fw-new-x")


@pytest.mark.asyncio
async def test_link_unmapped_merchant_to_new_payee(tmp_path):
    """Type an alias that doesn't match → confirm create → YNAB payee created + linked."""
    from finab.store import ConfigStore
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from finab.tui.widgets.yes_no_modal import YesNoModal
    from textual.widgets import ContentSwitcher

    store = ConfigStore(tmp_path / "config.json")
    txns = [_FakeFwTxn(merchant_id="fw-merch-x", merchant_name="WeirdMart")]
    ynab_payees = []
    ynab_client = _StubYnabClientForMerchants()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        ms = app.query_one(MerchantsScreen)
        ms.bind_data(
            store=store,
            fw_transactions=txns,
            ynab_payees=ynab_payees,
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        ms.set_cursor(0)  # unmapped row first
        ms.action_link()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        app.screen.dismiss("WeirdMart")
        await pilot.pause()
        assert isinstance(app.screen, YesNoModal)
        await pilot.press("y")
        await pilot.pause()
        assert len(ynab_client.created_payees) == 1
        m = store.merchant_by_alias("WeirdMart")
        assert m is not None
        # store.finwise is a dict keyed by fw_id
        assert "fw-merch-x" in m["finwise"]
