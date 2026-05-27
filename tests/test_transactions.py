import unittest
from unittest.mock import MagicMock
from datetime import date

from finab.transactions import (
    _is_inflow,
    _is_transfer,
    _find_inflow_category,
)
from finab.models import Transaction


class TestTransactionToYnab(unittest.TestCase):
    def test_empty_subtransactions_becomes_none(self):
        """Empty subtransactions on the wire makes YNAB think it's a split
        transaction with 0 splits and silently drops the category — leaving
        the transaction uncategorized and breaking next-run dedup. So empty
        list must become None."""
        t = Transaction(
            account_id="acc", date=date(2026, 1, 1), amount=-1000,
            category_id="cat-A", subtransactions=[],
        )
        y = t.to_ynab()
        self.assertIsNone(y.subtransactions)

    def test_non_empty_subtransactions_preserved(self):
        t = Transaction(
            account_id="acc", date=date(2026, 1, 1), amount=-1000,
            subtransactions=[{"category_id": "cat-A", "amount": -1000, "memo": ""}],
        )
        y = t.to_ynab()
        self.assertEqual(len(y.subtransactions), 1)


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
from finab.transactions import merge_and_filter_transactions, TransactionsStore


class TestMergeAndFilter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.tx_path = Path(self.tmpdir.name) / "transactions.json"
        self.store = ConfigStore(self.config_path)
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store = ConfigStore(self.config_path)
        self.tx_store = TransactionsStore(self.tx_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, import_id, account_id, amount, payee_name="X"):
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.payee_name = payee_name
        t.merchant_id = None
        t.memo = ""
        t.date = _date(2026, 5, 1)
        t.subtransactions = []
        t.payee_id = None
        t.category_id = None
        t.ynab_id = None
        return t

    def _ynab_txn(self, import_id, account_id, amount, category_id=None):
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.category_id = category_id
        t.deleted = False
        t.subtransactions = []
        t.transfer_account_id = None
        return t

    def test_unknown_account_skipped(self):
        fw = self._fw_txn("fw-1", "fw-unknown-acc", -1000)
        result = merge_and_filter_transactions([fw], [], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_new_transaction_assigned_import_id(self):
        fw = self._fw_txn("fw-1", "fw-acc", -1000)
        result = merge_and_filter_transactions([fw], [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertIsNotNone(result[0].import_id)

    def test_already_synced_and_resolved_skipped(self):
        """Resolved = YNAB category set or transfer account set. Already
        categorized transactions should not be re-processed."""
        fw = self._fw_txn("fw-1", "fw-acc", -1000)
        self.tx_store.record("fw-1", "iid-existing")
        yn = self._ynab_txn("iid-existing", "yn-acc", -1000, category_id="cat-A")
        result = merge_and_filter_transactions([fw], [yn], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_synced_but_uncategorized_returned_for_update(self):
        """If a YNAB twin exists but has no category, offer it for update."""
        fw = self._fw_txn("fw-1", "fw-acc", -1000)
        self.tx_store.record("fw-1", "iid-existing")
        yn = self._ynab_txn("iid-existing", "yn-acc", -1000, category_id=None)
        result = merge_and_filter_transactions([fw], [yn], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, str(yn.id))

    def test_skips_already_categorized_match(self):
        """A match where YNAB twin already has a category_id → skip."""
        fw = self._fw_txn("fw-1", "fw-acc", -1000)
        self.tx_store.record("fw-1", "iid-cat")
        yn = self._ynab_txn("iid-cat", "yn-acc", -1000, category_id="cat-X")
        result = merge_and_filter_transactions([fw], [yn], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_deleted_ynab_twin_rotates_import_id(self):
        """If the YNAB transaction was deleted by the user, the stored
        import_id should be rotated (new UUID) so the next push creates
        fresh rather than getting no-op'd by YNAB's ghost dedup."""
        fw = self._fw_txn("fw-1", "fw-acc", -1000)
        old_iid = "iid-deleted"
        self.tx_store.record("fw-1", old_iid)
        # YNAB returns no matching import_id → twin was deleted
        result = merge_and_filter_transactions([fw], [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        new_iid = result[0].import_id
        self.assertNotEqual(new_iid, old_iid)

    def test_ignore_transactions_account_skipped(self):
        """Accounts with ignore_transactions=True must not contribute any
        transactions to the candidate list."""
        self.store.add_account(
            alias="Savings",
            fw_record={"id": "fw-savings"},
            ynab_record={"id": "yn-savings", "transfer_payee_id": "tp-2"},
            ignore_transactions=True,
        )
        self.store = ConfigStore(self.config_path)
        fw = self._fw_txn("fw-2", "fw-savings", -500)
        result = merge_and_filter_transactions([fw], [], self.store, self.tx_store)
        self.assertEqual(result, [])


from finab.transactions import _closest_processing, _apply_repeat


class TestRepeatHelpers(unittest.TestCase):
    def test_closest_exact_match(self):
        merchant = {"processings": {"-1000": {"parent_memo": "", "splits": []}}}
        result = _closest_processing(merchant, MagicMock(amount=-1000))
        self.assertEqual(result[0], "-1000")

    def test_closest_picks_nearest_by_absolute_diff(self):
        merchant = {"processings": {
            "-1000": {"parent_memo": "", "splits": []},
            "-5000": {"parent_memo": "", "splits": []},
            "-10000": {"parent_memo": "", "splits": []},
        }}
        # -3000 is closer to -1000 (|2000|) than -5000 (|2000|) — tie goes to insertion order
        self.assertEqual(_closest_processing(merchant, MagicMock(amount=-3000))[0], "-1000")
        # -4000 is closer to -5000 (|1000|) than to -1000 (|3000|)
        self.assertEqual(_closest_processing(merchant, MagicMock(amount=-4000))[0], "-5000")
        # -8000 closest to -10000
        self.assertEqual(_closest_processing(merchant, MagicMock(amount=-8000))[0], "-10000")

    def test_closest_returns_none_when_no_processings(self):
        self.assertIsNone(_closest_processing({}, MagicMock(amount=-1000)))
        self.assertIsNone(_closest_processing({"processings": {}}, MagicMock(amount=-1000)))

    def test_apply_repeat_single_category_exact(self):
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ],
            }
        }}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.category_id, "cat-A")
        self.assertEqual(txn.subtransactions, [])
        self.assertEqual(txn.memo, "finwise desc")

    def test_apply_repeat_single_category_when_amount_differs(self):
        """Single-category entries replay the category regardless of
        txn.amount, since there's nothing to scale."""
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ],
            }
        }}
        txn = MagicMock(amount=-2500)  # closest (and only) entry is -1000
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.category_id, "cat-A")

    def test_apply_repeat_split_exact(self):
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
                    {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
                ],
            }
        }}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertIsNone(txn.category_id)
        self.assertEqual(len(txn.subtransactions), 2)
        self.assertEqual(txn.subtransactions[0]["amount"], -600)
        self.assertEqual(txn.subtransactions[1]["amount"], -400)

    def test_apply_repeat_scales_split_when_amount_differs(self):
        """Multi-split entries scale to the current txn amount when the
        closest prior amount differs."""
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
                    {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
                ],
            }
        }}
        txn = MagicMock(amount=-5000)  # 5x the prior total
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.subtransactions[0]["amount"], -3000)
        self.assertEqual(txn.subtransactions[1]["amount"], -2000)
        self.assertEqual(
            sum(s["amount"] for s in txn.subtransactions), txn.amount
        )

    def test_apply_repeat_picks_closest_among_many(self):
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ],
            },
            "-5000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-B", "amount_milliunits": -5000, "memo": ""}
                ],
            },
        }}
        txn = MagicMock(amount=-4500, memo="")  # closer to -5000
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.category_id, "cat-B")


from finab.transactions import _apply_processing_to_txn, _render_splits


class TestRenderSplits(unittest.TestCase):
    def _cats(self):
        def c(i, n):
            m = MagicMock(id=i)
            m.name = n
            return m
        return [c("cat-A", "Groceries"), c("cat-B", "Fuel")]

    def test_single_split_returns_category_name(self):
        entry = {"splits": [
            {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
        ]}
        self.assertEqual(_render_splits(entry, self._cats()), "Groceries")

    def test_multi_split_returns_amounts_and_names(self):
        entry = {"splits": [
            {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
            {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
        ]}
        rendered = _render_splits(entry, self._cats())
        self.assertIn("Groceries -0.60", rendered)
        self.assertIn("Fuel -0.40", rendered)

    def test_multi_split_scales_amounts(self):
        entry = {"splits": [
            {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
            {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
        ]}
        rendered = _render_splits(entry, self._cats(), scale=5.0)
        self.assertIn("Groceries -3.00", rendered)
        self.assertIn("Fuel -2.00", rendered)


from finab.transactions import _update_merchant_memory


class TestUpdateMerchantMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.merchant = self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _single_txn(self, amount, cat_id, memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = cat_id
        t.subtransactions = []
        t.memo = memo
        return t

    def _split_txn(self, amount, subs, parent_memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = None
        t.subtransactions = subs
        t.memo = parent_memo
        return t

    def test_single_category_writes_processings_keyed_by_amount(self):
        txn = self._single_txn(-50000, "cat-A", memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1})
        self.assertIn("-50000", m["processings"])
        entry = m["processings"]["-50000"]
        self.assertEqual(entry["parent_memo"], "receipt")
        self.assertEqual(len(entry["splits"]), 1)
        self.assertEqual(entry["splits"][0]["category_id"], "cat-A")
        self.assertEqual(entry["splits"][0]["amount_milliunits"], -50000)

    def test_split_increments_each_category(self):
        subs = [
            {"category_id": "cat-A", "amount": -30000, "memo": "fuel"},
            {"category_id": "cat-B", "amount": -20000, "memo": "snacks"},
        ]
        txn = self._split_txn(-50000, subs, parent_memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})
        entry = m["processings"]["-50000"]
        self.assertEqual(len(entry["splits"]), 2)
        self.assertEqual(entry["splits"][0]["category_id"], "cat-A")
        self.assertEqual(entry["splits"][0]["amount_milliunits"], -30000)
        self.assertEqual(entry["splits"][0]["memo"], "fuel")

    def test_distinct_amounts_accumulate_separate_entries(self):
        """Categorizing -1000 then -2000 for the same merchant produces
        TWO entries in processings, one per amount."""
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-2000, "cat-B"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(set(m["processings"].keys()), {"-1000", "-2000"})
        self.assertEqual(m["processings"]["-1000"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["processings"]["-2000"]["splits"][0]["category_id"], "cat-B")
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})

    def test_same_amount_recategorize_overwrites(self):
        """Categorizing -1000 as cat-A, then re-categorizing -1000 as cat-B,
        replaces the entry for -1000 (most recent wins per amount).
        categories_used reflects both uses cumulatively."""
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-1000, "cat-B"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(list(m["processings"].keys()), ["-1000"])
        self.assertEqual(m["processings"]["-1000"]["splits"][0]["category_id"], "cat-B")
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})


class TestIsBeforeCurrentMonth(unittest.TestCase):
    def test_before_current_month(self):
        from datetime import date as d
        from finab.transactions import _is_before_current_month
        txn = MagicMock(date=d(2026, 4, 30))
        self.assertTrue(_is_before_current_month(txn, today=d(2026, 5, 27)))

    def test_first_of_current_month(self):
        from datetime import date as d
        from finab.transactions import _is_before_current_month
        txn = MagicMock(date=d(2026, 5, 1))
        self.assertFalse(_is_before_current_month(txn, today=d(2026, 5, 27)))

    def test_today(self):
        from datetime import date as d
        from finab.transactions import _is_before_current_month
        txn = MagicMock(date=d(2026, 5, 27))
        self.assertFalse(_is_before_current_month(txn, today=d(2026, 5, 27)))

    def test_no_date_returns_false(self):
        from finab.transactions import _is_before_current_month
        txn = MagicMock(spec=[])  # no .date attribute
        self.assertFalse(_is_before_current_month(txn))


class TestMerchantMemoryStringifiesCategoryId(unittest.TestCase):
    """Regression: category ids from the YNAB SDK come back as UUID objects;
    they must be stringified before being stored as dict keys (JSON can't
    serialize UUID keys)."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.merchant = self.store.add_merchant(
            alias="Aperitif",
            fw_record={"id": "fw-ap", "name": "Aperitif"},
            ynab_record={"id": "yn-ap", "name": "Aperitif"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_uuid_category_id_stored_as_string(self):
        from uuid import UUID
        from finab.transactions import _update_merchant_memory
        # Simulate a category_id that is a UUID object (as returned by
        # the YNAB SDK).
        uuid_cat = UUID("12345678-1234-1234-1234-123456789abc")
        txn = MagicMock()
        txn.amount = -44000
        txn.category_id = uuid_cat
        txn.subtransactions = []
        txn.memo = "lunch"

        _update_merchant_memory(self.store, self.merchant, txn)

        # The store must be reloadable from disk — meaning the JSON dump
        # succeeded, i.e. the category_id was stringified.
        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-ap")
        self.assertEqual(m["categories_used"], {str(uuid_cat): 1})
        self.assertEqual(m["processings"]["-44000"]["splits"][0]["category_id"], str(uuid_cat))


class TestTransactionsStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "transactions.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_starts_empty(self):
        s = TransactionsStore(self.path)
        self.assertIsNone(s.import_id_for("anything"))

    def test_record_and_retrieve(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "import-id-1")
        self.assertEqual(s.import_id_for("fw-1"), "import-id-1")
        # Persists across reload
        s2 = TransactionsStore(self.path)
        self.assertEqual(s2.import_id_for("fw-1"), "import-id-1")

    def test_remove(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "import-id-1")
        s.remove("fw-1")
        self.assertIsNone(s.import_id_for("fw-1"))

    def test_prune_stale_removes_orphans(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "iid-keep")
        s.record("fw-2", "iid-gone")
        removed = s.prune_stale({"iid-keep"})
        self.assertEqual(removed, 1)
        self.assertEqual(s.import_id_for("fw-1"), "iid-keep")
        self.assertIsNone(s.import_id_for("fw-2"))


if __name__ == "__main__":
    unittest.main()
