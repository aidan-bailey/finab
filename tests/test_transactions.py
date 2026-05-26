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


from unittest.mock import patch
from finab.transactions import _pick_category


class TestPickCategory(unittest.TestCase):
    def _category(self, cid, name, hidden=False, deleted=False):
        # MagicMock(name=...) is reserved; assign .name explicitly.
        c = MagicMock(id=cid, hidden=hidden, deleted=deleted)
        c.name = name
        return c

    def test_pick_from_used_by_number(self):
        merchant = {"alias": "Spar", "categories_used": {"c-groceries": 47, "c-snacks": 3}}
        categories = [
            self._category("c-groceries", "Groceries"),
            self._category("c-snacks", "Snacks"),
        ]
        with patch("builtins.input", return_value="1"):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        # 47x is most-used, sorts first
        self.assertEqual(result, "c-groceries")

    def test_returns_none_when_back(self):
        merchant = {"alias": "Spar", "categories_used": {"c-groceries": 1}}
        categories = [self._category("c-groceries", "Groceries")]
        with patch("builtins.input", return_value="b"):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        self.assertIsNone(result)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    def test_out_of_range_reprompts(self, _stdout):
        merchant = {"alias": "Spar", "categories_used": {"c-groceries": 1}}
        categories = [self._category("c-groceries", "Groceries")]
        # 99 is out of range; reprompt; then 1 picks the only used category.
        with patch("builtins.input", side_effect=["99", "1"]):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        self.assertEqual(result, "c-groceries")


from finab.transactions import _pick_category_from_full_list


class TestPickCategoryFromFullList(unittest.TestCase):
    def _cat(self, cid, name, hidden=False, deleted=False):
        c = MagicMock(id=cid, hidden=hidden, deleted=deleted)
        c.name = name
        return c

    def _grp(self, gid, name, cats):
        g = MagicMock(id=gid)
        g.name = name
        g.categories = cats
        return g

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="2")
    def test_picks_by_global_number(self, _input, _stdout):
        groups = [
            self._grp("g1", "Bills", [self._cat("c1", "Internet"), self._cat("c2", "Power")]),
            self._grp("g2", "Fun",   [self._cat("c3", "Movies")]),
        ]
        self.assertEqual(_pick_category_from_full_list(groups), "c2")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="")
    def test_empty_input_returns_none(self, _input, _stdout):
        self.assertIsNone(_pick_category_from_full_list([]))

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="1")
    def test_skips_hidden_and_deleted(self, _input, _stdout):
        groups = [
            self._grp("g1", "X", [
                self._cat("c1", "Hidden", hidden=True),
                self._cat("c2", "Real"),
            ]),
        ]
        # Picks 1 -> the only non-hidden category
        self.assertEqual(_pick_category_from_full_list(groups), "c2")


from finab.transactions import _create_new_category


class TestCreateNewCategory(unittest.TestCase):
    def _grp(self, gid, name):
        g = MagicMock(id=gid)
        g.name = name
        g.categories = []
        return g

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["Pet Supplies", "1"])
    def test_creates_category_in_existing_group(self, _input, _stdout):
        groups = [self._grp("g1", "Bills"), self._grp("g2", "Fun")]
        client = MagicMock()
        new_cat = MagicMock(id="new-c", category_group_id="g1")
        new_cat.name = "Pet Supplies"
        client.create_category.return_value = new_cat

        result = _create_new_category(groups, client, "bid")

        self.assertEqual(result, "new-c")
        client.create_category.assert_called_once_with("bid", "Pet Supplies", "g1")
        client.create_category_group.assert_not_called()
        # Side effect: the group's categories should include the new one.
        self.assertIn(new_cat, groups[0].categories)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["Cleaning", "n", "Home Maintenance"])
    def test_creates_new_group_then_category(self, _input, _stdout):
        groups = []
        client = MagicMock()
        new_grp = MagicMock(id="new-g")
        new_grp.name = "Home Maintenance"
        new_grp.categories = []
        new_cat = MagicMock(id="new-c", category_group_id="new-g")
        new_cat.name = "Cleaning"
        client.create_category_group.return_value = new_grp
        client.create_category.return_value = new_cat

        result = _create_new_category(groups, client, "bid")

        self.assertEqual(result, "new-c")
        client.create_category_group.assert_called_once_with("bid", "Home Maintenance")
        client.create_category.assert_called_once_with("bid", "Cleaning", "new-g")
        # Both the new group and new category should be appended to the in-memory list.
        self.assertIn(new_grp, groups)
        self.assertIn(new_cat, new_grp.categories)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="")
    def test_empty_name_returns_none(self, _input, _stdout):
        result = _create_new_category([], MagicMock(), "bid")
        self.assertIsNone(result)


from finab.transactions import _prompt_memo


class TestPromptMemo(unittest.TestCase):
    @patch("builtins.input", return_value="")
    def test_empty_uses_default(self, _input):
        self.assertEqual(_prompt_memo("default note"), "default note")

    @patch("builtins.input", return_value="")
    def test_empty_default_returns_empty(self, _input):
        self.assertEqual(_prompt_memo(""), "")

    @patch("builtins.input", return_value="custom note")
    def test_typed_value_replaces_default(self, _input):
        self.assertEqual(_prompt_memo("default note"), "custom note")

    @patch("builtins.input", return_value="  spaced  ")
    def test_strips_whitespace(self, _input):
        self.assertEqual(_prompt_memo(""), "spaced")


from finab.transactions import _collect_splits


class TestCollectSplits(unittest.TestCase):
    def _txn(self, amount):
        t = MagicMock()
        t.amount = amount
        return t

    def _category(self, cid, name):
        c = MagicMock(id=cid, hidden=False, deleted=False)
        c.name = name
        return c

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[
        # split count
        "2",
        # split 1 amount, category number, memo
        "60", "1", "fuel only",
        # split 2 amount (default = remaining), category number, memo
        "", "2", "",
    ])
    def test_collects_two_splits_with_remaining_default(self, _input, _stdout):
        txn = self._txn(-100000)  # -100.00
        merchant = {"alias": "Spar", "categories_used": {"c-petrol": 5, "c-snacks": 1}}
        categories = [self._category("c-petrol", "Petrol"), self._category("c-snacks", "Snacks")]
        splits = _collect_splits(txn, merchant, categories, [], MagicMock(), "bid")
        self.assertIsNotNone(splits)
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0]["category_id"], "c-petrol")
        self.assertEqual(splits[0]["amount_milliunits"], -60000)
        self.assertEqual(splits[0]["memo"], "fuel only")
        self.assertEqual(splits[1]["category_id"], "c-snacks")
        self.assertEqual(splits[1]["amount_milliunits"], -40000)
        self.assertEqual(splits[1]["memo"], "")
        # All splits sum to txn.amount
        self.assertEqual(sum(s["amount_milliunits"] for s in splits), txn.amount)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["0"])
    def test_invalid_count_returns_none(self, _input, _stdout):
        txn = self._txn(-1000)
        merchant = {"alias": "X", "categories_used": {}}
        result = _collect_splits(txn, merchant, [], [], MagicMock(), "bid")
        self.assertIsNone(result)


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

    def test_single_category_updates_count_and_last(self):
        txn = self._single_txn(-50000, "cat-A", memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1})
        self.assertEqual(m["last_processing"]["amount_milliunits"], -50000)
        self.assertEqual(m["last_processing"]["parent_memo"], "receipt")
        self.assertEqual(len(m["last_processing"]["splits"]), 1)
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["last_processing"]["splits"][0]["amount_milliunits"], -50000)

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
        self.assertEqual(len(m["last_processing"]["splits"]), 2)
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["last_processing"]["splits"][0]["amount_milliunits"], -30000)
        self.assertEqual(m["last_processing"]["splits"][0]["memo"], "fuel")

    def test_increment_runs_cumulatively(self):
        # First processing
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        # Reload and do another
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-2000, "cat-A"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"]["cat-A"], 2)


if __name__ == "__main__":
    unittest.main()
