import unittest
from unittest.mock import MagicMock

from finab.transactions import (
    _PendingQueue,
    _is_inflow,
    _is_transfer,
    _find_inflow_category,
)


class TestPendingQueue(unittest.TestCase):
    def test_starts_empty(self):
        q = _PendingQueue()
        self.assertEqual(q.count(), 0)

    def test_add_routes_by_ynab_id_presence(self):
        q = _PendingQueue()
        create_txn = MagicMock(ynab_id=None)
        update_txn = MagicMock(ynab_id="yn-existing")
        q.add(create_txn)
        q.add(update_txn)
        self.assertEqual(q.count(), 2)
        self.assertEqual(len(q.creates), 1)
        self.assertEqual(len(q.updates), 1)

    def test_flush_calls_both_apis_and_clears(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        q.add(MagicMock(ynab_id="yn-1"))
        client = MagicMock()
        ok = q.flush(client, "bid")
        self.assertTrue(ok)
        client.create_transactions.assert_called_once()
        client.update_transactions.assert_called_once()
        self.assertEqual(q.count(), 0)

    def test_flush_with_only_creates(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        ok = q.flush(client, "bid")
        self.assertTrue(ok)
        client.create_transactions.assert_called_once()
        client.update_transactions.assert_not_called()

    def test_flush_failure_keeps_queue(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        client.create_transactions.side_effect = RuntimeError("network")
        ok = q.flush(client, "bid")
        self.assertFalse(ok)
        self.assertEqual(q.count(), 1)


class TestAutoPathHelpers(unittest.TestCase):
    def test_is_inflow_positive(self):
        self.assertTrue(_is_inflow(MagicMock(amount=1000)))
        self.assertFalse(_is_inflow(MagicMock(amount=-1000)))
        self.assertFalse(_is_inflow(MagicMock(amount=0)))

    def test_is_transfer_when_merchant_has_transfer_account_id(self):
        m = {"ynab": {"id": "yn-tp", "transfer_account_id": "yn-acc-1"}}
        self.assertTrue(_is_transfer(m))

    def test_is_transfer_false_when_missing(self):
        self.assertFalse(_is_transfer({"ynab": {"id": "yn-p"}}))
        self.assertFalse(_is_transfer({"ynab": {}}))
        self.assertFalse(_is_transfer(None))

    def _category(self, id, name, hidden=False, deleted=False):
        # MagicMock(name=...) sets the mock's debug name, not the .name attr.
        # Construct then assign to get a real .name attribute.
        c = MagicMock(id=id, hidden=hidden, deleted=deleted)
        c.name = name
        return c

    def test_find_inflow_category_prefers_ready_to_assign(self):
        cats = [
            self._category("c1", "Inflow: To be Budgeted"),
            self._category("c2", "Inflow: Ready to Assign"),
        ]
        self.assertEqual(_find_inflow_category(cats), "c2")

    def test_find_inflow_category_skips_hidden_or_deleted(self):
        cats = [
            self._category("c1", "Inflow: Ready to Assign", hidden=True),
            self._category("c2", "Inflow: Ready to Assign", deleted=True),
        ]
        self.assertIsNone(_find_inflow_category(cats))

    def test_find_inflow_category_returns_none_when_absent(self):
        cats = [self._category("c1", "Groceries")]
        self.assertIsNone(_find_inflow_category(cats))


from datetime import date as _date
import tempfile
from pathlib import Path

from finab.store import ConfigStore
from finab.transactions import merge_and_filter_transactions


class TestMergeAndFilter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, import_id, account_id, amount, payee_name="X"):
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.date = _date(2026, 5, 20)
        t.payee_name = payee_name
        t.ynab_id = None
        t.category_id = None
        return t

    def _ynab_txn(self, id, import_id, amount, category_id=None):
        t = MagicMock()
        t.id = id
        t.import_id = import_id
        t.amount = amount
        t.category_id = category_id
        t.deleted = False
        t.transfer_account_id = None
        return t

    def test_skips_fw_with_unknown_account(self):
        # Account fw-OTHER is not in the store
        fw_txns = [self._fw_txn("fw-tx-1", "fw-OTHER", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store)
        self.assertEqual(result, [])

    def test_maps_account_via_store(self):
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].account_id, "yn-acc")

    def test_skips_already_categorized_match(self):
        from finab.config import load_import_id_offset
        from finab.main import generate_import_id
        offset = load_import_id_offset()
        hashed = generate_import_id("fw-tx-1", offset)
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", hashed, -1000, category_id="cat-X")]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store)
        # Already categorized in YNAB -> skipped
        self.assertEqual(result, [])

    def test_links_uncategorized_ynab_match_for_update(self):
        from finab.config import load_import_id_offset
        from finab.main import generate_import_id
        offset = load_import_id_offset()
        hashed = generate_import_id("fw-tx-1", offset)
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", hashed, -1000, category_id=None)]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "yn-tx-1")


if __name__ == "__main__":
    unittest.main()
