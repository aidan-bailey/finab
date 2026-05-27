import unittest
from unittest.mock import MagicMock
from datetime import date

from finab.transactions import (
    _PendingQueue,
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

    def test_flush_failure_raises(self):
        """Failures propagate. The queue may be in any state on failure;
        the contract is just 'an exception reaches the orchestrator'."""
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        client.create_transactions.side_effect = RuntimeError("network")
        with self.assertRaises(RuntimeError):
            q.flush(client, "bid")


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
        t.date = _date(2026, 5, 20)
        t.payee_name = payee_name
        t.ynab_id = None
        t.category_id = None
        return t

    def _ynab_txn(self, id, amount, category_id=None, deleted=False, import_id=None):
        t = MagicMock()
        t.id = id
        t.amount = amount
        t.category_id = category_id
        t.deleted = deleted
        t.transfer_account_id = None
        t.import_id = import_id
        return t

    def test_skips_fw_with_unknown_account(self):
        fw_txns = [self._fw_txn("fw-tx-1", "fw-OTHER", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_maps_account_via_store(self):
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].account_id, "yn-acc")

    def test_skips_already_categorized_match(self):
        """A FW txn whose stored import_id matches a YNAB txn that is
        already categorized is dropped (preserve manual YNAB edits)."""
        self.tx_store.record("fw-tx-1", "import-id-A")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn(
            "yn-tx-1", -1000, category_id="cat-X", import_id="import-id-A"
        )]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_skips_transfer_match_even_without_category(self):
        """YNAB transfers legitimately have no category — the link IS the
        resolution. Don't re-prompt the user for them on every sync."""
        self.tx_store.record("fw-tx-1", "import-id-A")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab = self._ynab_txn("yn-tx-1", -1000, category_id=None, import_id="import-id-A")
        ynab.transfer_account_id = "yn-other-acc"  # flags as transfer
        result = merge_and_filter_transactions(fw_txns, [ynab], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_skips_tracking_account_match_even_without_category(self):
        """Tracking (off-budget) accounts in YNAB don't use categories.
        A null category on a tracking-account transaction is normal, not a
        signal to re-prompt the user."""
        # Add a tracking-type account in the store.
        self.store.add_account(
            alias="EasyEquities",
            fw_record={"id": "fw-track"},
            ynab_record={"id": "yn-track", "type": "otherAsset"},
        )
        self.store = ConfigStore(self.config_path)

        self.tx_store.record("fw-tx-2", "import-id-T")
        fw_txns = [self._fw_txn("fw-tx-2", "fw-track", -1000)]
        ynab = self._ynab_txn("yn-tx-2", -1000, category_id=None, import_id="import-id-T")
        result = merge_and_filter_transactions(fw_txns, [ynab], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_links_uncategorized_ynab_match_for_update(self):
        """A FW txn whose stored import_id matches an uncategorized YNAB
        txn is marked for update (ynab_id set, import_id kept stable)."""
        self.tx_store.record("fw-tx-1", "import-id-A")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn(
            "yn-tx-1", -1000, category_id=None, import_id="import-id-A"
        )]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "yn-tx-1")
        self.assertEqual(result[0].import_id, "import-id-A")

    def test_rotates_when_ynab_twin_was_deleted(self):
        """If the stored import_id is no longer present in the live YNAB
        fetch, the entry is replaced with a fresh uuid and the FW txn is
        treated as new (will re-push with the new id)."""
        self.tx_store.record("fw-tx-1", "import-id-OLD")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        # YNAB no longer has any txn with import_id=import-id-OLD.
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].ynab_id)
        rotated = self.tx_store.import_id_for("fw-tx-1")
        self.assertIsNotNone(rotated)
        self.assertNotEqual(rotated, "import-id-OLD")
        self.assertEqual(result[0].import_id, rotated)

    def test_skips_account_marked_ignore_transactions(self):
        """Transactions whose FW account is flagged ignore_transactions
        are dropped — they never reach the YNAB sync."""
        accounts = list(self.store.accounts())
        self.store._data["accounts"][accounts[0]["id"]]["ignore_transactions"] = True
        self.store._save()
        self.store = ConfigStore(self.config_path)

        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(result, [])

    def test_assigns_fresh_uuid_for_never_synced(self):
        """A FW txn with no stored mapping gets a fresh uuid recorded and
        set as its import_id."""
        fw_txns = [self._fw_txn("fw-tx-new", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        assigned = self.tx_store.import_id_for("fw-tx-new")
        self.assertIsNotNone(assigned)
        self.assertEqual(result[0].import_id, assigned)
        self.assertIsNone(result[0].ynab_id)


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


from finab.transactions import _pick_from_processings, _apply_processing_to_txn, _render_splits
from unittest.mock import patch
import io


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


class TestPickFromProcessings(unittest.TestCase):
    def _cats(self):
        def c(i, n):
            m = MagicMock(id=i)
            m.name = n  # `name=` in MagicMock kwargs names the mock itself, not .name
            return m
        return [c("cat-A", "Groceries"), c("cat-B", "Fuel"), c("cat-C", "Eating Out")]

    def test_returns_false_and_no_mutation_when_empty(self):
        merchant = {"alias": "Spar", "processings": {}}
        txn = MagicMock(amount=-1000)
        txn.subtransactions = []
        txn.category_id = None
        self.assertFalse(_pick_from_processings(merchant, txn, self._cats()))
        self.assertIsNone(txn.category_id)
        self.assertEqual(txn.subtransactions, [])

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, return_value="")
    def test_empty_input_backs_out(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ]},
            },
        }
        txn = MagicMock(amount=-2500)
        txn.subtransactions = []
        txn.category_id = None
        self.assertFalse(_pick_from_processings(merchant, txn, self._cats()))
        self.assertIsNone(txn.category_id)

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, return_value="1")
    def test_picks_single_category_replay(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ]},
            },
        }
        txn = MagicMock(amount=-7500)  # different amount — single-cat replays directly
        txn.subtransactions = ["leftover"]
        txn.category_id = None
        self.assertTrue(_pick_from_processings(merchant, txn, self._cats()))
        self.assertEqual(txn.category_id, "cat-A")
        self.assertEqual(txn.subtransactions, [])

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, return_value="1")
    def test_picks_multi_split_scales_proportionally(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
                    {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
                ]},
            },
        }
        txn = MagicMock(amount=-5000)  # 5x the prior total
        txn.subtransactions = []
        txn.category_id = None
        self.assertTrue(_pick_from_processings(merchant, txn, self._cats()))
        self.assertIsNone(txn.category_id)
        self.assertEqual(len(txn.subtransactions), 2)
        self.assertEqual(txn.subtransactions[0]["amount"], -3000)
        self.assertEqual(txn.subtransactions[1]["amount"], -2000)
        # Totals reconcile exactly
        self.assertEqual(
            sum(s["amount"] for s in txn.subtransactions), txn.amount
        )

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, return_value="1")
    def test_split_rounding_absorbed_into_last(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -333, "memo": ""},
                    {"category_id": "cat-B", "amount_milliunits": -333, "memo": ""},
                    {"category_id": "cat-C", "amount_milliunits": -334, "memo": ""},
                ]},
            },
        }
        # Pick a current amount that doesn't divide evenly by the prior splits.
        txn = MagicMock(amount=-1001)
        txn.subtransactions = []
        txn.category_id = None
        self.assertTrue(_pick_from_processings(merchant, txn, self._cats()))
        # Sum must equal txn.amount exactly (rounding absorbed into last).
        self.assertEqual(
            sum(s["amount"] for s in txn.subtransactions), txn.amount
        )

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, side_effect=["1"])
    def test_picks_first_entry_when_multiple(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ]},
                "-5000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-B", "amount_milliunits": -5000, "memo": ""}
                ]},
            },
        }
        txn = MagicMock(amount=-9999)
        txn.subtransactions = []
        txn.category_id = None
        self.assertTrue(_pick_from_processings(merchant, txn, self._cats()))
        self.assertEqual(txn.category_id, "cat-A")

    @patch("sys.stdout", new_callable=io.StringIO)
    @patch("finab.transactions.input", create=True, side_effect=["99", "1"])
    def test_out_of_range_reprompts(self, _input, _stdout):
        merchant = {
            "alias": "Spar",
            "processings": {
                "-1000": {"parent_memo": "", "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ]},
            },
        }
        txn = MagicMock(amount=-1000)
        txn.subtransactions = []
        txn.category_id = None
        self.assertTrue(_pick_from_processings(merchant, txn, self._cats()))
        self.assertEqual(txn.category_id, "cat-A")


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


from unittest.mock import patch
from finab.transactions import _process_one_transaction


class TestProcessOneTransaction(unittest.TestCase):
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
        self.ynab_client = MagicMock()
        self.category = MagicMock(id="c-petrol", hidden=False, deleted=False)
        self.category.name = "Petrol"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _txn(self, amount, merchant_id="fw-spar"):
        t = MagicMock()
        t.amount = amount
        t.merchant_id = merchant_id
        t.memo = "spar receipt"
        t.payee_id = None
        t.payee_name = None
        t.category_id = None
        t.subtransactions = []
        return t

    def test_inflow_auto_categorizes(self):
        txn = self._txn(50000)  # positive
        inflow_cat = MagicMock(id="c-inflow", hidden=False, deleted=False)
        inflow_cat.name = "Inflow: Ready to Assign"
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [inflow_cat], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-inflow")

    def test_no_merchant_returns_categorized_uncategorized(self):
        txn = self._txn(-1000, merchant_id=None)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertIsNone(txn.category_id)

    def test_transfer_merchant_sets_payee_no_category(self):
        # Create a transfer-payee merchant
        self.store.add_merchant(
            alias="Discovery Bank ZAR",
            fw_record={"id": "fw-xfer", "name": "Discovery"},
            ynab_record={"id": "yn-xfer-payee", "transfer_account_id": "yn-acc"},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-2000, merchant_id="fw-xfer")
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.payee_id, "yn-xfer-payee")
        self.assertIsNone(txn.category_id)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["c", "1", ""])
    def test_interactive_single_category(self, _input, _stdout):
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={
                "-9999": {  # mismatched so no Enter-repeat
                    "parent_memo": "",
                    "splits": [{"category_id": "c-petrol",
                                "amount_milliunits": -9999, "memo": ""}],
                }
            },
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)

        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-petrol")
        # Payee resolved from merchant
        self.assertEqual(txn.payee_id, "yn-spar")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="f")
    def test_user_picks_flush(self, _input, _stdout):
        # Seed memory so an Enter-repeat would otherwise be offered.
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={"-1000": {"parent_memo": "",
                                   "splits": [{"category_id": "c-petrol",
                                               "amount_milliunits": -1000, "memo": ""}]}},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 1, 3, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "flush")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="")
    def test_enter_replays_last_processing_when_amount_matches(self, _input, _stdout):
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={"-3000": {"parent_memo": "",
                                   "splits": [{"category_id": "c-petrol",
                                               "amount_milliunits": -3000, "memo": ""}]}},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-petrol")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="q")
    def test_user_picks_quit(self, _input, _stdout):
        """Typing 'q' in the per-transaction prompt returns 'quit' so the
        orchestrator can break the loop and finish Phase 3."""
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={"-3000": {"parent_memo": "",
                                   "splits": [{"category_id": "c-petrol",
                                               "amount_milliunits": -3000,
                                               "memo": ""}]}},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 5, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "quit")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    def test_warns_when_finwise_says_transfer_but_merchant_not_linked(self, _stdout):
        """If FinWise marks the txn as a transfer but our merchant isn't
        linked as a transfer-payee, print a warning so the user can fix it."""
        txn = self._txn(-3000)  # has merchant 'Spar', not a transfer
        txn.is_transfer = True
        # Pre-month so it auto-pushes without prompting and the test stays linear.
        from datetime import date as d
        txn.date = d(2020, 1, 1)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        # Pre-month path returns "categorized" after pushing with payee.
        self.assertEqual(outcome, "categorized")
        out = _stdout.getvalue()
        self.assertIn("FinWise marks this as a transfer", out)
        self.assertIn("Spar", out)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    def test_no_warning_when_finwise_does_not_say_transfer(self, _stdout):
        txn = self._txn(-3000)
        txn.is_transfer = False
        from datetime import date as d
        txn.date = d(2020, 1, 1)
        _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertNotIn("FinWise marks this as a transfer", _stdout.getvalue())


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


class TestPreCurrentMonthAutoPath(unittest.TestCase):
    """A transaction whose date is before the current month should be pushed
    with payee resolved from the merchant, but with no category and no
    interactive prompt."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from finab.store import ConfigStore
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

    def test_old_outflow_pushes_with_payee_no_category(self):
        from datetime import date as d
        from unittest.mock import patch
        from finab.transactions import _process_one_transaction

        txn = MagicMock()
        txn.amount = -5000
        txn.merchant_id = "fw-spar"
        txn.memo = "old purchase"
        txn.date = d(2026, 4, 15)
        txn.subtransactions = []
        txn.payee_id = None
        txn.payee_name = None
        txn.category_id = None

        # Force "before current month" to True without touching the date class.
        with patch("finab.transactions._is_before_current_month", return_value=True):
            outcome = _process_one_transaction(
                txn, 1, 1, 0, self.store, MagicMock(), "bid", [], []
            )

        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.payee_id, "yn-spar")
        self.assertIsNone(txn.category_id)
        self.assertEqual(txn.subtransactions, [])


class TestMemoryGateOnMissingDecision(unittest.TestCase):
    """sync_transactions must not call _update_merchant_memory for
    transactions that ended up with no category (transfers, no-merchant,
    pre-current-month auto-pushes). Updating memory there would clobber
    a previous legitimate processings entry."""

    def setUp(self):
        import tempfile
        from pathlib import Path
        from finab.store import ConfigStore
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        # Seed an account so merge_and_filter doesn't filter the txn out.
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        # Seed a transfer-payee merchant: any txn for this merchant will
        # take the transfer auto-path and return category_id=None.
        self.merchant = self.store.add_merchant(
            alias="Discovery Bank ZAR",
            fw_record={"id": "fw-xfer", "name": "Discovery"},
            ynab_record={"id": "yn-xfer-payee", "transfer_account_id": "yn-acc-2"},
        )
        # Seed a pre-existing processings entry on the merchant — we want to
        # verify it's NOT overwritten.
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"cat-existing": 3},
            processings={
                "-1234": {
                    "parent_memo": "previous",
                    "splits": [{"category_id": "cat-existing",
                                "amount_milliunits": -1234, "memo": ""}],
                }
            },
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_transfer_does_not_overwrite_last_processing(self):
        from datetime import date as d
        from finab.transactions import sync_transactions

        fw_client = MagicMock()
        ynab_client = MagicMock()
        txn = MagicMock()
        txn.import_id = "fw-tx-1"
        txn.account_id = "fw-acc"
        txn.amount = -9999
        txn.merchant_id = "fw-xfer"
        txn.memo = ""
        txn.date = d(2026, 5, 20)
        txn.subtransactions = []
        txn.payee_id = None
        txn.payee_name = None
        txn.category_id = None
        txn.ynab_id = None
        fw_client.get_transactions.return_value = [txn]
        ynab_client.get_transactions.return_value = []
        ynab_client.get_categories.return_value = []
        ynab_client.get_category_groups_with_categories.return_value = []

        sync_transactions(fw_client, ynab_client, "bid", self.store)

        m = self.store.merchant_by_finwise_id("fw-xfer")
        # The pre-existing memory must remain untouched.
        self.assertEqual(m["categories_used"], {"cat-existing": 3})
        self.assertIn("-1234", m["processings"])
        self.assertEqual(m["processings"]["-1234"]["splits"][0]["category_id"], "cat-existing")


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


class TestPendingQueueFlushPassesImportId(unittest.TestCase):
    """After the stable-import_id refactor, flush sends txn.import_id
    (already a durable UUID set by merge_and_filter_transactions) directly
    to YNAB — no transient correlators, no post-flush mapping writes."""

    def test_flush_sends_txn_import_id_as_is(self):
        from finab.transactions import _PendingQueue
        q = _PendingQueue()
        t1 = MagicMock(ynab_id=None, import_id="stable-uuid-1")
        t2 = MagicMock(ynab_id=None, import_id="stable-uuid-2")
        q.add(t1)
        q.add(t2)

        ynab_client = MagicMock()
        q.flush(ynab_client, "bid")

        # The import_id on each txn was NOT mutated by flush.
        self.assertEqual(t1.import_id, "stable-uuid-1")
        self.assertEqual(t2.import_id, "stable-uuid-2")
        ynab_client.create_transactions.assert_called_once()

    def test_flush_works_with_real_transaction_model(self):
        """Regression: pydantic v2's Transaction model rejects undeclared
        attribute assignment. The new flush is simpler — no side-attr
        stash — so this is just a smoke test that real Transaction
        instances flow through cleanly."""
        from datetime import date as d
        from finab.models import Transaction
        from finab.transactions import _PendingQueue

        q = _PendingQueue()
        txn = Transaction(
            account_id="yn-acc",
            date=d(2026, 5, 20),
            amount=-5000,
            import_id="stable-uuid",
        )
        q.add(txn)
        ynab_client = MagicMock()
        q.flush(ynab_client, "bid")
        self.assertEqual(txn.import_id, "stable-uuid")


class TestFailLoudFlush(unittest.TestCase):
    """A YNAB API exception during flush must propagate so the user sees
    the traceback and a non-zero exit. Silently swallowing was the
    behaviour that caused a categorize-everything-then-nothing-in-YNAB
    bug previously."""

    def test_flush_exception_propagates(self):
        from finab.transactions import _PendingQueue
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None, import_id="x"))
        ynab_client = MagicMock()
        ynab_client.create_transactions.side_effect = RuntimeError("YNAB 500")
        with self.assertRaises(RuntimeError) as cm:
            q.flush(ynab_client, "bid")
        self.assertIn("YNAB 500", str(cm.exception))

    def test_partial_failure_clears_succeeded_list(self):
        """If create_transactions succeeds but update_transactions raises,
        the creates list should already be cleared so a retry doesn't
        re-post the same creates."""
        from finab.transactions import _PendingQueue
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None, import_id="iid-create"))
        q.add(MagicMock(ynab_id="yn-existing", import_id="iid-update"))
        ynab_client = MagicMock()
        ynab_client.update_transactions.side_effect = RuntimeError("YNAB 500 on update")
        with self.assertRaises(RuntimeError):
            q.flush(ynab_client, "bid")
        # Creates was successfully pushed — must be cleared.
        self.assertEqual(len(q.creates), 0)
        # Updates failed — remains in the queue for retry.
        self.assertEqual(len(q.updates), 1)


class TestFailLoudSyncTransactionsFetch(unittest.TestCase):
    """A fetch failure during sync_transactions must propagate."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store = ConfigStore(self.config_path)
        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_fw_get_transactions_failure_propagates(self):
        from finab.transactions import sync_transactions
        self.fw_client.get_transactions.side_effect = RuntimeError("FW down")
        with self.assertRaises(RuntimeError):
            sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

    def test_ynab_get_categories_failure_propagates(self):
        from finab.transactions import sync_transactions
        self.fw_client.get_transactions.return_value = []
        self.ynab_client.get_transactions.return_value = []
        self.ynab_client.get_categories.side_effect = RuntimeError("YNAB down")
        with self.assertRaises(RuntimeError):
            sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)


if __name__ == "__main__":
    unittest.main()
