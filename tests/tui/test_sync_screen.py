"""Pilot-driven tests for the Sync screen."""
import pytest


@pytest.mark.asyncio
async def test_sync_screen_has_two_panes():
    """SyncScreen has a pending-list pane (left) and a detail pane (right)."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        pending_pane = app.query_one("#sync-pending")
        detail_pane = app.query_one("#sync-detail")
        assert pending_pane is not None
        assert detail_pane is not None


@pytest.mark.asyncio
async def test_transaction_card_shows_empty_state():
    """No candidates → card shows empty-state text."""
    from textual.widgets import Static
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        card = app.query_one("#sync-detail")
        # Card is now a Container; read text from inner Statics.
        text = ""
        for s in card.query(Static):
            text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        assert "select a transaction" in text.lower() or "no transaction" in text.lower()


@pytest.mark.asyncio
async def test_transaction_card_renders_candidate():
    """Card renders the candidate's fields after one is selected."""
    from datetime import date as _date
    from textual.widgets import Static
    from finab.engine.sync import Candidate
    from finab.tui.app import FinabApp

    class FakeTxn:
        amount = -84210  # milliunits: -84.21
        memo = "COSTCO WHSE #1234"
        date = _date(2026, 5, 22)
        category_id = None
        subtransactions = []

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one("#screen-sync")
        sync_screen.set_candidates(
            [Candidate(id="abc", txn=FakeTxn(), status="pending")],
            alias_of=lambda c: "Costco",
        )
        await pilot.pause()
        card = app.query_one("#sync-detail")
        # Card is now a Container — aggregate text from inner Statics.
        card_text = ""
        for s in card.query(Static):
            card_text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        # Merchant alias is now uppercased.
        assert "COSTCO" in card_text
        # Amount appears without "Amount:" label.
        assert "-84.21" in card_text
        # Date appears in meta line.
        assert "2026-05-22" in card_text
        # Memo still appears.
        assert "COSTCO WHSE" in card_text


@pytest.mark.asyncio
async def test_sync_screen_builds_engine_from_loaded_data(tmp_path):
    """When SyncScreen.bind_data is called with LoadedData, a SyncEngine
    is built and the candidates appear in the PendingList."""
    from datetime import date as date_cls
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.models import Transaction
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    # Seed a ConfigStore so the engine finds a mapped account.
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    txn = Transaction(
        import_id="fw-1",
        amount=-84210,  # milliunits
        date=date_cls.today(),
        memo="COSTCO",
        merchant_id=None,
        account_id="fw-acc-1",
    )
    pre_loaded = LoadedData(
        fw_accounts=[],
        fw_transactions=[txn],
        ynab_accounts=[],
        ynab_transactions=[],
        ynab_categories=[],
        ynab_category_groups=[],
        ynab_payees=[],
    )

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=pre_loaded, store=store, tx_store=tx_store)
        await pilot.pause()
        # One candidate should be present. With no merchant linked, the
        # engine marks it pending/no-merchant (not auto — user must act).
        pl = app.query_one("#sync-pending", PendingList)
        assert len(pl.candidates) == 1
        assert pl.candidates[0].status == "pending"
        assert pl.candidates[0].auto_reason == "no-merchant"


@pytest.mark.asyncio
async def test_pressing_c_opens_category_picker(tmp_path):
    """Pressing 'c' on a pending candidate pushes CategoryPickerModal."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.category_picker import CategoryPickerModal

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-84210,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # Focus the SyncScreen so its 'c' binding is active.
        sync_screen.focus()
        # The pending list inside SyncScreen needs to have a candidate
        # selected. After bind_data, the first candidate is set, but
        # the list cursor might be unset — force-select index 0.
        from finab.tui.widgets.pending_list import PendingList
        pl = app.query_one("#sync-pending", PendingList)
        pl.focus()
        if pl.index is None and len(pl.candidates) > 0:
            pl.index = 0
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CategoryPickerModal)


@pytest.mark.asyncio
async def test_u_undoes_the_current_row(tmp_path):
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-84210,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # Decide first.
        sync_screen._engine.apply_category(sync_screen._engine.candidates[0].id, category_id="cat-x")
        # Set list cursor.
        pl = app.query_one("#sync-pending", PendingList)
        if pl.index is None:
            pl.index = 0
        await pilot.pause()
        # Press u to undo.
        await pilot.press("u")
        await pilot.pause()
        c = sync_screen._engine.candidates[0]
        assert c.status == "pending"


@pytest.mark.asyncio
async def test_f_calls_flush(tmp_path):
    """Pressing f calls engine.flush with the stub ynab_client and budget_id from FinabApp."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen

    flushed = {"called": False}

    class _StubYnab:
        def create_transactions(self, budget_id, txns): flushed["called"] = True
        def update_transactions(self, budget_id, txns): pass

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-84210,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp(ynab_client=_StubYnab(), budget_id="bid")
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # Decide first.
        sync_screen._engine.apply_category(sync_screen._engine.candidates[0].id, category_id="cat-x")
        await pilot.pause()
        # Press f to flush.
        await pilot.press("f")
        await pilot.pause()
        assert flushed["called"] is True


@pytest.mark.asyncio
async def test_pressing_enter_applies_closest_history(tmp_path):
    """Enter on a candidate with a closest-amount history entry should
    apply it via engine.apply_history."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    store.set_merchant_memory(
        store.merchant_by_alias("Costco")["id"],
        categories_used={"cat-groc": 3},
        processings={"-8421": {"parent_memo": "x", "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}]}},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-e1",
        amount=-8421,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=LoadedData(fw_transactions=[txn]), store=store, tx_store=tx_store)
        await pilot.pause()
        pl = app.query_one("#sync-pending", PendingList)
        if pl.index is None:
            pl.index = 0
        pl.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        c = sync_screen._engine.candidates[0]
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groc"


@pytest.mark.asyncio
async def test_pressing_g_jumps_to_top(tmp_path):
    """g moves the cursor to row 0."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txns = [
        Transaction(import_id=f"fw-g{i}", amount=-1000-i, date=today, memo=f"M{i}", merchant_id=None, account_id="fw-acc-1")
        for i in range(3)
    ]
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=LoadedData(fw_transactions=txns), store=store, tx_store=tx_store)
        await pilot.pause()
        pl = app.query_one("#sync-pending", PendingList)
        pl.index = 2
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert pl.index == 0
