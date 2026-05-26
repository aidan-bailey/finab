import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore
from finab.transactions import sync_transactions


class TestSyncTransactionsIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        # Seed accounts and merchants for a complete run.
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, import_id, account_id, amount, merchant_id, memo=""):
        from datetime import date
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.merchant_id = merchant_id
        t.date = date(2026, 5, 20)
        t.memo = memo
        t.subtransactions = []
        t.payee_id = None
        t.payee_name = None
        t.category_id = None
        t.ynab_id = None
        return t

    def _category(self, cid, name, hidden=False, deleted=False):
        c = MagicMock(id=cid, hidden=hidden, deleted=deleted)
        c.name = name
        return c

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["c", "1", ""])
    def test_full_flow_pushes_at_end(self, _input, _stdout):
        # One outflow transaction
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-tx-1", "fw-acc", -5000, "fw-spar", memo="purchase"),
        ]
        self.ynab_client.get_transactions.return_value = []
        # One eligible category, also used to seed picker
        cat = self._category("c-groceries", "Groceries")
        self.ynab_client.get_categories.return_value = [cat]
        self.ynab_client.get_category_groups_with_categories.return_value = []
        # Seed merchant memory so the picker shows the used category
        m = self.store.merchant_by_finwise_id("fw-spar")
        self.store.set_merchant_memory(
            m["id"],
            categories_used={"c-groceries": 1},
            last_processing={"amount_milliunits": -9999, "parent_memo": "",
                             "splits": [{"category_id": "c-groceries",
                                         "amount_milliunits": -9999, "memo": ""}]},
        )
        self.store = ConfigStore(self.path)

        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

        # Auto-flush at end should have pushed.
        self.ynab_client.create_transactions.assert_called_once()
        # Memory got updated.
        m2 = self.store.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m2["categories_used"]["c-groceries"], 2)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[])
    def test_positive_amount_auto_inflows(self, _input, _stdout):
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-tx-1", "fw-acc", 10000, "fw-spar"),
        ]
        self.ynab_client.get_transactions.return_value = []
        inflow = self._category("c-inflow", "Inflow: Ready to Assign")
        self.ynab_client.get_categories.return_value = [inflow]
        self.ynab_client.get_category_groups_with_categories.return_value = []

        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_transactions.assert_called_once()
        args = self.ynab_client.create_transactions.call_args
        pushed = args.args[1]
        self.assertEqual(pushed[0].category_id, "c-inflow")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[])
    def test_no_transactions_to_process(self, _input, _stdout):
        self.fw_client.get_transactions.return_value = []
        self.ynab_client.get_transactions.return_value = []
        self.ynab_client.get_categories.return_value = []
        self.ynab_client.get_category_groups_with_categories.return_value = []

        # Should not raise; should not push anything.
        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)
        self.ynab_client.create_transactions.assert_not_called()
        self.ynab_client.update_transactions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
