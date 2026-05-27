"""Tests for finab.engine.sync.SyncEngine and Candidate.

These exercise the headless state machine — no Textual, no client calls.
SyncEngine.flush is tested separately with a stub client.
"""
from dataclasses import is_dataclass
from pathlib import Path
from typing import Optional

import pytest

from finab.engine.sync import Candidate, SyncEngine
from finab.models import Transaction
from finab.store import ConfigStore
from finab.transactions import TransactionsStore


class TestCandidate:
    def test_is_a_dataclass(self):
        assert is_dataclass(Candidate)

    def test_default_status_is_pending(self):
        c = Candidate(id="abc", txn=object())
        assert c.status == "pending"
        assert c.auto_reason is None
        assert c.prior_state is None

    def test_can_set_status_and_auto_reason(self):
        c = Candidate(id="abc", txn=object(), status="auto", auto_reason="inflow")
        assert c.status == "auto"
        assert c.auto_reason == "inflow"


class _FakeCategory:
    """Minimal stub matching the YNAB SDK Category shape used by
    _find_inflow_category. Only `id`, `name`, `hidden`, `deleted` are read.
    """
    def __init__(self, id, name, *, hidden=False, deleted=False):
        self.id = id
        self.name = name
        self.hidden = hidden
        self.deleted = deleted


def _build_txn(
    *,
    fw_uuid: str,
    amount: int,
    merchant_id: Optional[str] = None,
    account_id: str,
    date_str: str = "2026-05-22",
    memo: str = "test",
    is_transfer: bool = False,
):
    """Construct a Transaction matching what FinWiseClient produces."""
    from datetime import date as date_cls
    y, m, d = (int(x) for x in date_str.split("-"))
    return Transaction(
        import_id=fw_uuid,
        amount=amount,
        date=date_cls(y, m, d),
        memo=memo,
        merchant_id=merchant_id,
        account_id=account_id,
        is_transfer=is_transfer,
    )


def _seeded_store(tmp_path: Path) -> ConfigStore:
    """Return a ConfigStore with one mapped account and no merchants."""
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    return store


class TestSyncEngineLoad:
    def test_empty_inputs_produces_empty_candidates(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        engine = SyncEngine(
            fw_transactions=[],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert engine.candidates == []

    def test_inflow_sets_status_auto(self, tmp_path):
        """A positive-amount transaction should auto-resolve when an
        'Inflow: Ready to Assign' category exists."""
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-1", amount=12345, account_id="fw-acc-1")
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "inflow"
        assert str(c.txn.category_id) == "cat-rta"

    def test_inflow_with_no_category_falls_through(self, tmp_path):
        """A positive-amount transaction with NO inflow category in YNAB
        must fall through to the merchant-resolution logic rather than
        silently auto-mark as inflow with a None category."""
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        # Positive amount + no merchant + no inflow category → no-merchant auto.
        txn = _build_txn(
            fw_uuid="fw-pos-1", amount=99999,
            account_id="fw-acc-1", merchant_id=None,
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],  # explicitly empty — no inflow category
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "no-merchant"  # fall-through landed here
        assert c.txn.category_id is None

    def test_no_merchant_sets_status_auto(self, tmp_path):
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-2", amount=-4200, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "no-merchant"

    def test_transfer_sets_status_auto(self, tmp_path):
        """A txn whose merchant is linked to an account (transfer_account_id
        set on the merchant's YNAB record) should auto-resolve as transfer."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        # Add a merchant whose YNAB record IS a transfer payee.
        store.add_merchant(
            alias="Self → Savings",
            fw_record={"id": "fw-merchant-transfer", "name": "Self → Savings", "samples": []},
            ynab_record={
                "id": "yn-pay-transfer",
                "name": "Transfer: Savings",
                "transfer_account_id": "yn-savings-acc",
            },
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-transfer", amount=-25000,
            account_id="fw-acc-1", merchant_id="fw-merchant-transfer",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "transfer"
        assert c.txn.payee_id == "yn-pay-transfer"
        assert c.txn.category_id is None

    def test_unknown_account_is_dropped_by_dedup(self, tmp_path):
        """merge_and_filter_transactions drops txns whose account isn't
        mapped — those should not appear as candidates at all."""
        store = ConfigStore(tmp_path / "config.json")  # no accounts mapped
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-3", amount=-1000, account_id="fw-unknown")
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert engine.candidates == []

    def test_pre_current_month_sets_status_auto(self, tmp_path):
        """A txn dated before the first of the current month, with a
        known merchant, should auto-resolve with reason='pre-month'."""
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="OldShop",
            fw_record={"id": "fw-merchant-1", "name": "OldShop", "samples": []},
            ynab_record={"id": "yn-pay-1", "name": "OldShop", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        # 2000-01-01 is unambiguously before 'today' in this codebase's lifetime
        txn = _build_txn(
            fw_uuid="fw-4", amount=-500,
            account_id="fw-acc-1", merchant_id="fw-merchant-1",
            date_str="2000-01-01",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "pre-month"

    def test_unresolved_txn_stays_pending(self, tmp_path):
        """Current-month txn with a known merchant that isn't a transfer
        payee — engine has no auto-rule for it, user must decide."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "pending"
        assert c.auto_reason is None


class TestApplyCategory:
    def _setup(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        return engine, store

    def test_marks_decided_and_sets_category(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groceries", memo="produce")
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groceries"
        assert c.txn.subtransactions == []
        assert c.txn.memo == "produce"

    def test_writes_merchant_memory(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groceries")
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert merchant["categories_used"].get("cat-groceries") == 1
        assert str(c.txn.amount) in merchant["processings"]

    def test_snapshots_prior_state(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        original_payee_id = c.txn.payee_id
        engine.apply_category(c.id, category_id="cat-groceries")
        assert c.prior_state is not None
        assert c.prior_state["payee_id"] == original_payee_id
        assert c.prior_state["category_id"] is None  # was None pre-decision

    def test_unknown_candidate_id_raises(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        with pytest.raises(KeyError):
            engine.apply_category("not-a-real-id", category_id="cat-x")


class TestApplySplit:
    def _setup(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        return engine, store

    def test_split_sets_subtransactions(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        c = engine.candidates[0]
        splits = [
            {"category_id": "cat-groc", "amount": -5000, "memo": "produce"},
            {"category_id": "cat-house", "amount": -3421, "memo": "soap"},
        ]
        engine.apply_split(c.id, splits=splits)
        assert c.status == "decided"
        assert c.txn.category_id is None
        assert len(c.txn.subtransactions) == 2
        assert c.txn.subtransactions[0]["amount"] == -5000

    def test_split_must_sum_to_total(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        c = engine.candidates[0]
        bad_splits = [
            {"category_id": "cat-groc", "amount": -1000, "memo": ""},
            {"category_id": "cat-house", "amount": -1000, "memo": ""},
        ]
        with pytest.raises(ValueError, match="must sum"):
            engine.apply_split(c.id, splits=bad_splits)

    def test_split_writes_merchant_memory(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_split(c.id, splits=[
            {"category_id": "cat-groc", "amount": -5000, "memo": ""},
            {"category_id": "cat-house", "amount": -3421, "memo": ""},
        ])
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        # both categories should be counted
        assert merchant["categories_used"].get("cat-groc") == 1
        assert merchant["categories_used"].get("cat-house") == 1


class TestApplyTransfer:
    def test_apply_transfer_sets_payee_and_clears_category(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-6", amount=-15000,
            account_id="fw-acc-1", merchant_id=None,
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        # Currently this txn is auto/no-merchant — override it to a transfer.
        engine.apply_transfer(c.id, transfer_payee_id="yn-tpayee-1")
        assert c.status == "decided"
        assert c.txn.payee_id == "yn-tpayee-1"
        assert c.txn.category_id is None
        assert c.txn.subtransactions == []

    def test_apply_transfer_does_not_touch_merchant_memory(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-7", amount=-9999,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        engine.apply_transfer(c.id, transfer_payee_id="yn-tpayee-1")
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert not merchant.get("categories_used")
        assert not merchant.get("processings")
