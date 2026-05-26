import unittest
import hashlib
from unittest.mock import MagicMock, patch
from datetime import date
import sys
import os

# Stub modules
if "pydantic" not in sys.modules:
    mock_pydantic = MagicMock()
    mock_pydantic.BaseModel = object
    mock_pydantic.Field = MagicMock()
    sys.modules["pydantic"] = mock_pydantic

if "dotenv" not in sys.modules:
    sys.modules["dotenv"] = MagicMock()

# Mock finab.client and finab.ynab_client to avoid their imports
mock_finab_client = MagicMock()
sys.modules["finab.client"] = mock_finab_client

mock_ynab_client_module = MagicMock()
sys.modules["finab.ynab_client"] = mock_ynab_client_module

# Now import main
from finab.main import merge_and_filter_transactions, generate_import_id


class TestHashingAndMatching(unittest.TestCase):
    def setUp(self):
        self.offset = "test_offset"
        # Mock load_import_id_offset to return our test offset
        patcher = patch("finab.main.load_import_id_offset", return_value=self.offset)
        self.mock_offset = patcher.start()
        self.addCleanup(patcher.stop)

        self.fw_id_to_ynab_id = {"fw_acc_1": "ynab_acc_1"}

        # Helper to create mock account
        self.ynab_account = MagicMock()
        self.ynab_account.transfer_payee_id = "transfer_id"
        self.ynab_accounts = [self.ynab_account]

    def test_generate_import_id(self):
        """Test the generate_import_id helper function directly."""
        original_id = "original_id"
        offset = "test_offset"
        expected_hash = hashlib.sha256(
            (original_id + offset).encode("utf-8")
        ).hexdigest()[:36]

        result = generate_import_id(original_id, offset)
        self.assertEqual(result, expected_hash)
        self.assertEqual(len(result), 36)

    def test_hashed_import_id_generation(self):
        fw_txn = MagicMock()
        fw_txn.account_id = "fw_acc_1"
        fw_txn.import_id = "original_id"
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        expected_hash = hashlib.sha256(
            ("original_id" + self.offset).encode("utf-8")
        ).hexdigest()[:36]

        result = merge_and_filter_transactions(
            [fw_txn], [], self.fw_id_to_ynab_id, self.ynab_accounts
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].import_id, expected_hash)

    def test_match_by_hashed_import_id(self):
        original_id = "original_id"
        hashed_id = hashlib.sha256(
            (original_id + self.offset).encode("utf-8")
        ).hexdigest()[:36]

        fw_txn = MagicMock()
        fw_txn.account_id = "fw_acc_1"
        fw_txn.import_id = original_id
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        ynab_txn = MagicMock()
        ynab_txn.import_id = hashed_id
        ynab_txn.id = "ynab_txn_id"
        ynab_txn.transfer_account_id = None
        ynab_txn.category_id = None  # Uncategorized
        ynab_txn.deleted = False
        ynab_txn.var_date = date(2023, 1, 1)  # New SDK uses var_date
        ynab_txn.amount = 1000
        ynab_txn.payee_name = "Test Payee"

        result = merge_and_filter_transactions(
            [fw_txn], [ynab_txn], self.fw_id_to_ynab_id, self.ynab_accounts
        )

        # Should match and set ynab_id
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "ynab_txn_id")

    def test_fallback_match_by_fuzzy_for_migration(self):
        """Test that fuzzy matching works as fallback for migration from old format."""
        original_id = "original_id"
        old_style_id = "some_old_format_id"  # Old format that won't match hash

        fw_txn = MagicMock()
        fw_txn.account_id = "fw_acc_1"
        fw_txn.import_id = original_id
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        ynab_txn = MagicMock()
        ynab_txn.import_id = old_style_id  # Won't match the hash
        ynab_txn.id = "ynab_txn_id"
        ynab_txn.transfer_account_id = None
        ynab_txn.category_id = None
        ynab_txn.deleted = False
        ynab_txn.var_date = date(2023, 1, 1)  # Matches (new SDK uses var_date)
        ynab_txn.amount = 1000  # Matches
        ynab_txn.payee_name = "Test Payee"  # Matches

        result = merge_and_filter_transactions(
            [fw_txn], [ynab_txn], self.fw_id_to_ynab_id, self.ynab_accounts
        )

        # Should match via fuzzy fallback and set ynab_id
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "ynab_txn_id")
        # Should have the NEW hashed import_id for migration
        expected_hash = hashlib.sha256(
            (original_id + self.offset).encode("utf-8")
        ).hexdigest()[:36]
        self.assertEqual(result[0].import_id, expected_hash)

    def test_no_match_different_amount(self):
        """Test that fuzzy matching doesn't match when amount differs."""
        fw_txn = MagicMock()
        fw_txn.account_id = "fw_acc_1"
        fw_txn.import_id = "new_id"
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        ynab_txn = MagicMock()
        ynab_txn.import_id = "different_id"
        ynab_txn.id = "ynab_txn_id"
        ynab_txn.transfer_account_id = None
        ynab_txn.category_id = None
        ynab_txn.deleted = False
        ynab_txn.var_date = date(2023, 1, 1)  # Matches (new SDK uses var_date)
        ynab_txn.amount = 2000  # Different
        ynab_txn.payee_name = "Test Payee"  # Matches

        result = merge_and_filter_transactions(
            [fw_txn], [ynab_txn], self.fw_id_to_ynab_id, self.ynab_accounts
        )

        # Should NOT match because amount differs
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].ynab_id)


if __name__ == "__main__":
    unittest.main()
