import unittest
from unittest.mock import MagicMock, patch
from datetime import date
import sys
import os

# Adjust path to find finab source if running from root
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from finab.main import sync_transactions
from finab.models import Transaction


class TestSyncTransactions(unittest.TestCase):
    @patch("finab.main.load_aliases")
    @patch("finab.main.load_payee_rules")
    @patch("finab.main.load_merchant_aliases")
    @patch("finab.main.load_category_rules")
    @patch("finab.main.load_salt")
    @patch("builtins.print")
    @patch("builtins.input")
    def test_sync_transactions_flow(
        self,
        mock_input,
        mock_print,
        mock_salt,
        mock_cat_rules,
        mock_merch_aliases,
        mock_payee_rules,
        mock_aliases,
    ):
        # Setup Mocks
        mock_aliases.return_value = {}
        mock_payee_rules.return_value = []
        mock_merch_aliases.return_value = {}
        mock_cat_rules.return_value = {}
        mock_salt.return_value = "salt"
        mock_input.return_value = ""  # Default to empty input (skip/ignore)

        # Clients
        mock_fw_client = MagicMock()
        mock_ynab_client = MagicMock()
        budget_id = "test-budget"

        # Data
        # Accounts
        fw_acc = MagicMock()
        fw_acc.finwise_id = "fw-1"
        fw_acc.name = "Bank"
        mock_fw_client.get_accounts.return_value = [fw_acc]

        ynab_acc = MagicMock()
        ynab_acc.ynab_id = "ynab-1"
        ynab_acc.name = "Bank"
        mock_ynab_client.get_accounts.return_value = [ynab_acc]

        # Categories & Payees
        mock_ynab_client.get_categories.return_value = []
        mock_ynab_client.get_payees.return_value = []

        # Transactions
        # A transaction that needs to be synced
        fw_txn = MagicMock(spec=Transaction)
        fw_txn.account_id = "fw-1"
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = -1000
        fw_txn.payee_name = "Shop"
        fw_txn.memo = "Shop Purchase"
        fw_txn.merchant_id = None
        fw_txn.import_id = "import-1"
        fw_txn.category_id = None
        fw_txn.payee_id = None

        mock_fw_client.get_transactions.return_value = [fw_txn]
        mock_ynab_client.get_transactions.return_value = []  # No existing txns

        # Run
        sync_transactions(mock_fw_client, mock_ynab_client, budget_id)

        # Verify
        # Should call create_transactions
        mock_ynab_client.create_transactions.assert_called_once()
        call_args = mock_ynab_client.create_transactions.call_args
        self.assertEqual(call_args[0][0], budget_id)
        txns_to_create = call_args[0][1]
        self.assertEqual(len(txns_to_create), 1)
        self.assertEqual(txns_to_create[0].account_id, "ynab-1")  # mapped ID
        # The payee name might be truncated or processed, but should be "Shop" here
        self.assertEqual(txns_to_create[0].payee_name, "Shop")


if __name__ == "__main__":
    unittest.main()
