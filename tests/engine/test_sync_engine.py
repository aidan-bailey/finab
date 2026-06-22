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

    def test_candidate_has_transfer_fields_defaulting_none(self):
        c = Candidate(id="x", txn=object())
        assert c.transfer_partner_id is None
        assert c.transfer_role is None
        assert c.transfer_dest_alias is None

    def test_candidate_accepts_merged_status_and_transfer_reason(self):
        c = Candidate(
            id="x", txn=object(), status="merged",
            auto_reason="transfer-merged", transfer_role="suppress",
            transfer_partner_id="y", transfer_dest_alias="Savings",
        )
        assert c.status == "merged"
        assert c.auto_reason == "transfer-merged"
        assert c.transfer_role == "suppress"


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
        fw_uuid=fw_uuid,
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

    def test_inflow_with_no_category_falls_through_to_pending(self, tmp_path):
        """A positive-amount transaction with NO inflow category in YNAB
        must fall through to the merchant-resolution logic rather than
        silently auto-mark as inflow with a None category.
        With no merchant, it becomes pending/no-merchant (not auto)."""
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        # Positive amount + no merchant + no inflow category → pending/no-merchant.
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
        assert c.status == "pending"
        assert c.auto_reason == "no-merchant"  # fall-through landed here
        assert c.txn.category_id is None

    def test_no_merchant_sets_status_pending(self, tmp_path):
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
        assert c.status == "pending"
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

    def test_pre_current_month_sets_status_pending(self, tmp_path):
        """A txn dated before the first of the current month, with a
        known merchant, becomes pending (not auto) so flush() skips it."""
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
        assert c.status == "pending"
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


class TestUndo:
    def _setup_decided(self, tmp_path):
        """Build an engine with one candidate, apply a category, return it."""
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
            fw_uuid="fw-8", amount=-8421,
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
        engine.apply_category(c.id, category_id="cat-groc", memo="weekly")
        return engine, c, store

    def test_undo_restores_prior_state(self, tmp_path):
        engine, c, _ = self._setup_decided(tmp_path)
        # Prior to apply_category, this candidate was pending with no category.
        engine.undo(c.id)
        assert c.status == "pending"
        assert c.txn.category_id is None
        assert c.prior_state is None

    def test_undo_does_not_revert_merchant_memory(self, tmp_path):
        engine, c, store = self._setup_decided(tmp_path)
        engine.undo(c.id)
        # Memory write from apply_category persists — by design.
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert merchant["categories_used"].get("cat-groc") == 1

    def test_undo_raises_on_auto_candidate(self, tmp_path):
        """undo() raises on any non-decided candidate, including auto (inflow)."""
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        # Use a positive-amount txn with an inflow category to get a true auto candidate.
        txn = _build_txn(fw_uuid="fw-9", amount=100, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert c.status == "auto"
        with pytest.raises(ValueError, match="cannot undo"):
            engine.undo(c.id)


class _FakeYnabClient:
    """Tracks calls to create_transactions / update_transactions."""

    def __init__(self, fail_on=None):
        self.created = []  # list of lists
        self.updated = []
        self.fail_on = fail_on  # "create" or "update" to simulate failure

    def create_transactions(self, budget_id, txns):
        if self.fail_on == "create":
            raise RuntimeError("simulated create failure")
        self.created.append(list(txns))

    def update_transactions(self, budget_id, txns):
        if self.fail_on == "update":
            raise RuntimeError("simulated update failure")
        self.updated.append(list(txns))


class TestFlush:
    def _setup_engine_with_decisions(self, tmp_path, *, with_existing_ynab_id=False):
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
            fw_uuid="fw-10", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        if with_existing_ynab_id:
            txn.ynab_id = "yn-existing-123"
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groc")
        return engine, c

    def test_flush_pushes_creates(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path)
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.created) == 1
        assert client.created[0][0] is c.txn
        assert client.updated == []
        assert c.status == "flushed"

    def test_flush_pushes_updates(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path, with_existing_ynab_id=True)
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.updated) == 1
        assert client.updated[0][0] is c.txn
        assert client.created == []
        assert c.status == "flushed"

    def test_flush_skips_pending_and_flushed(self, tmp_path):
        """pending candidates and already-flushed ones aren't pushed."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        store.add_merchant(
            alias="Amazon",
            fw_record={"id": "fw-merchant-3", "name": "Amazon", "samples": []},
            ynab_record={"id": "yn-pay-3", "name": "Amazon", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        d = f"{today.year:04d}-{today.month:02d}-{today.day:02d}"
        txn_decided = _build_txn(fw_uuid="fw-11", amount=-1000, account_id="fw-acc-1", merchant_id="fw-merchant-2", date_str=d)
        txn_pending = _build_txn(fw_uuid="fw-12", amount=-2000, account_id="fw-acc-1", merchant_id="fw-merchant-3", date_str=d)
        engine = SyncEngine(
            fw_transactions=[txn_decided, txn_pending],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        # Decide one, leave the other pending.
        engine.apply_category(engine.candidates[0].id, category_id="cat-x")
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.created[0]) == 1  # only the decided one
        # Second flush is a no-op (decided one is now flushed, other is still pending).
        engine.flush(client, budget_id="bid")
        assert len(client.created) == 1   # no new batches

    def test_flush_failure_leaves_candidates_in_pre_flush_state(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path)
        client = _FakeYnabClient(fail_on="create")
        with pytest.raises(RuntimeError, match="simulated"):
            engine.flush(client, budget_id="bid")
        assert c.status == "decided"  # not flushed


class TestCandidateWarnings:
    def test_fw_transfer_with_unlinked_merchant_gets_warning(self, tmp_path):
        """When FinWise marks the txn as a transfer but the merchant isn't
        linked to an account's transfer payee, _build_candidate should
        populate `warnings` so the UI can render ⚠."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-wat", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-w1", amount=-5000,
            account_id="fw-acc-1", merchant_id="fw-merchant-wat",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
            is_transfer=True,
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert len(c.warnings) >= 1
        assert any("transfer" in w.lower() for w in c.warnings)


class TestApplyHistory:
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
            fw_uuid="fw-h1", amount=-8421,
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
        return engine

    def test_apply_history_single_split(self, tmp_path):
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        entry = {
            "parent_memo": "weekly",
            "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groc"
        assert c.prior_state is not None

    def test_apply_history_multi_split_scales(self, tmp_path):
        """apply_history scales multi-split amounts to current txn.amount."""
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        entry = {
            "parent_memo": "x",
            "splits": [
                {"category_id": "cat-a", "amount_milliunits": -6000, "memo": ""},
                {"category_id": "cat-b", "amount_milliunits": -4000, "memo": ""},
            ],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        assert c.txn.category_id is None
        assert len(c.txn.subtransactions) == 2
        total = sum(s["amount"] for s in c.txn.subtransactions)
        assert total == c.txn.amount

    def test_apply_history_supports_undo(self, tmp_path):
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        entry = {
            "parent_memo": "x",
            "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        engine.undo(c.id)
        assert c.status == "pending"
        assert c.txn.category_id is None


class TestTransferMatchingInEngine:
    def _two_account_store(self, tmp_path):
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
        return store

    def test_high_confidence_pair_keeps_one_suppresses_other(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        assert keep.status == "auto" and keep.auto_reason == "transfer-pair"
        assert keep.txn.payee_id == "tp-b" and keep.txn.category_id is None
        assert keep.transfer_dest_alias == "Savings"
        assert sup.status == "merged" and sup.auto_reason == "transfer-merged"
        assert keep.transfer_partner_id == sup.id and sup.transfer_partner_id == keep.id

    def test_inflow_side_not_booked_as_income(self, tmp_path):
        """Regression: the inflow rule must not claim the suppressed side."""
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        assert sup.auto_reason != "inflow"
        assert sup.status == "merged"

    def test_low_confidence_pair_is_pending_suggested(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10", merchant_id="x")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-11", merchant_id="y")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
            store=store, tx_store=tx_store, transfer_match_window_days=1,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        assert keep.status == "pending" and keep.auto_reason == "transfer-suggested"
        assert keep.txn.payee_id == "tp-b"

    def test_confirm_suggested_transfer_marks_decided(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10", merchant_id="x")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-11", merchant_id="y")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
            store=store, tx_store=tx_store, transfer_match_window_days=1,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        engine.confirm_transfer_match(keep.id)
        assert keep.status == "decided"
        assert keep.txn.payee_id == "tp-b"

    def test_undo_transfer_reverts_both_sides(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        engine.undo(keep.id)
        # Both lose their transfer role; suppress side re-enters normal rules.
        assert keep.transfer_role is None and sup.transfer_role is None
        assert keep.status == "pending"          # outflow, no merchant → no-merchant
        assert sup.status == "auto" and sup.auto_reason == "inflow"   # inflow reclaimed

    def test_undo_transfer_from_suppress_side_reverts_both(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        engine.undo(sup.id)   # undo triggered from the suppressed (inflow) side
        assert keep.transfer_role is None and sup.transfer_role is None
        assert keep.status == "pending"
        assert sup.status == "auto" and sup.auto_reason == "inflow"

    def test_flush_records_suppressed_side_and_marks_flushed(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        # Only the keep side was pushed (one create); suppressed side never sent.
        assert len(client.created) == 1 and len(client.created[0]) == 1
        assert client.created[0][0] is keep.txn
        # Suppressed FW uuid now maps to the kept side's import_id.
        assert tx_store.import_id_for("i") == keep.txn.import_id
        assert keep.status == "flushed" and sup.status == "flushed"

    def test_suppressed_partner_recorded_before_a_failing_update_batch(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        # A separate non-transfer txn forced onto the UPDATE path (pre-set ynab_id).
        upd = _build_txn(fw_uuid="u", amount=-1234, account_id="fw-a", date_str="2026-05-10")
        upd.ynab_id = "yn-existing-1"
        engine = SyncEngine(
            fw_transactions=[out, inn, upd], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        upd_c = next(c for c in engine.candidates if c.txn.fw_uuid == "u")
        engine.apply_category(upd_c.id, category_id="cat-x")
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        client = _FakeYnabClient(fail_on="update")
        with pytest.raises(RuntimeError, match="simulated"):
            engine.flush(client, budget_id="bid")
        # Creates batch (transfer keep) succeeded and its partner was recorded
        # BEFORE the failing update batch — so no duplicate on the next sync.
        assert keep.status == "flushed"
        assert sup.status == "flushed"
        assert tx_store.import_id_for("i") == keep.txn.import_id


class TestNoMerchantAndPreMonthAreBlocked:
    """The user explicitly disabled auto-pushing of no-merchant and pre-month
    candidates so they don't surprise YNAB. These paths should set
    status='pending' so flush() skips them."""

    def test_no_merchant_is_pending_not_auto(self, tmp_path):
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-nm", amount=-1000, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert c.status == "pending"
        assert c.auto_reason == "no-merchant"  # reason still populated for UI glyph

    def test_pre_month_is_pending_not_auto(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="OldShop",
            fw_record={"id": "fw-merchant-1", "name": "OldShop", "samples": []},
            ynab_record={"id": "yn-pay-1", "name": "OldShop", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(
            fw_uuid="fw-old", amount=-500,
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
        c = engine.candidates[0]
        assert c.status == "pending"
        assert c.auto_reason == "pre-month"

    def test_flush_skips_no_merchant_pending(self, tmp_path):
        """The whole point of the change: no-merchant candidates don't reach flush."""
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-nm-flush", amount=-2000, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )

        class _FakeYnabClient:
            def __init__(self):
                self.created = []
                self.updated = []
            def create_transactions(self, budget_id, txns):
                self.created.append(list(txns))
            def update_transactions(self, budget_id, txns):
                self.updated.append(list(txns))

        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        # Nothing should have been pushed.
        assert client.created == []
        assert client.updated == []
        # Candidate is still pending after flush (not flushed).
        assert engine.candidates[0].status == "pending"
