from pathlib import Path
from datetime import date

from finab.engine.sync import SyncEngine
from finab.models import Transaction
from finab.store import ConfigStore
from finab.transactions import TransactionsStore
from finab.tui.screens.sync import SyncScreen


def _engine(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Cheque",
        fw_record={"id": "fw-a", "name": "Cheque", "type": "checking", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-a", "name": "Cheque", "type": "checking", "balance": 0, "transfer_payee_id": "tp-a"},
    )
    store.add_account(
        alias="Savings",
        fw_record={"id": "fw-b", "name": "Savings", "type": "savings", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-b", "name": "Savings", "type": "savings", "balance": 0, "transfer_payee_id": "tp-b"},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    out = Transaction(import_id="o", fw_uuid="o", amount=-50000, date=date(2026, 5, 10),
                      account_id="fw-a", merchant_id="x", memo="m")
    inn = Transaction(import_id="i", fw_uuid="i", amount=50000, date=date(2026, 5, 11),
                      account_id="fw-b", merchant_id="y", memo="m")
    return SyncEngine(
        fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
        store=store, tx_store=tx_store, transfer_match_window_days=1,
    ), store


def test_accept_suggested_transfer_confirms_without_picker(tmp_path):
    """If the current candidate is a suggested transfer, `t` confirms it
    in-place rather than opening the account picker."""
    engine, store = _engine(tmp_path)
    keep = next(c for c in engine.candidates if c.transfer_role == "keep")

    screen = SyncScreen.__new__(SyncScreen)   # avoid Textual mount
    screen._engine = engine
    screen._store = store
    opened = {"picker": False}
    screen._current_candidate = lambda: keep
    screen._refresh_after_decision = lambda cid: None
    # Stub the picker path so we can assert it is NOT taken.
    def _fail_picker(*a, **k):
        opened["picker"] = True
    screen._open_force_transfer_picker = _fail_picker

    screen.action_force_transfer()
    assert keep.status == "decided"
    assert opened["picker"] is False
    assert keep.auto_reason == "transfer-pair"   # promoted from suggested on confirm
