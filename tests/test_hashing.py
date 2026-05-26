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

# Import generate_import_id from main (still lives there) and
# merge_and_filter_transactions from its new home in transactions.
from finab.main import generate_import_id
from finab.transactions import merge_and_filter_transactions


def _make_store(fw_acc_id: str, ynab_acc_id: str) -> MagicMock:
    """Return a minimal ConfigStore mock for merge_and_filter_transactions."""
    store = MagicMock()
    store.account_by_finwise_id.return_value = {"ynab": {"id": ynab_acc_id}}
    return store


class TestHashingAndMatching(unittest.TestCase):
    def setUp(self):
        self.offset = "test_offset"
        # load_import_id_offset is called via a local import inside
        # merge_and_filter_transactions, so we patch it at the source.
        patcher = patch("finab.config.load_import_id_offset", return_value=self.offset)
        self.mock_offset = patcher.start()
        self.addCleanup(patcher.stop)

        self.fw_acc_id = "fw_acc_1"
        self.ynab_acc_id = "ynab_acc_1"
        self.store = _make_store(self.fw_acc_id, self.ynab_acc_id)

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
        fw_txn.account_id = self.fw_acc_id
        fw_txn.import_id = "original_id"
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        expected_hash = hashlib.sha256(
            ("original_id" + self.offset).encode("utf-8")
        ).hexdigest()[:36]

        result = merge_and_filter_transactions([fw_txn], [], self.store)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].import_id, expected_hash)

    def test_match_by_hashed_import_id(self):
        original_id = "original_id"
        hashed_id = hashlib.sha256(
            (original_id + self.offset).encode("utf-8")
        ).hexdigest()[:36]

        fw_txn = MagicMock()
        fw_txn.account_id = self.fw_acc_id
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
        ynab_txn.var_date = date(2023, 1, 1)
        ynab_txn.amount = 1000
        ynab_txn.payee_name = "Test Payee"

        result = merge_and_filter_transactions([fw_txn], [ynab_txn], self.store)

        # Should match and set ynab_id
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "ynab_txn_id")

    def test_no_match_different_import_id(self):
        """Test that transactions with non-matching import_ids don't get ynab_id set."""
        fw_txn = MagicMock()
        fw_txn.account_id = self.fw_acc_id
        fw_txn.import_id = "new_id"
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = 1000
        fw_txn.payee_name = "Test Payee"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        ynab_txn = MagicMock()
        ynab_txn.import_id = "completely_different_hashed_id"
        ynab_txn.id = "ynab_txn_id"
        ynab_txn.transfer_account_id = None
        ynab_txn.category_id = None
        ynab_txn.deleted = False
        ynab_txn.var_date = date(2023, 1, 1)
        ynab_txn.amount = 1000
        ynab_txn.payee_name = "Test Payee"

        result = merge_and_filter_transactions([fw_txn], [ynab_txn], self.store)

        # Should NOT match because import_ids differ (no fuzzy fallback in new design)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].ynab_id)

    def test_skip_already_categorized(self):
        """Transactions already categorized in YNAB should be skipped."""
        original_id = "original_id"
        hashed_id = hashlib.sha256(
            (original_id + self.offset).encode("utf-8")
        ).hexdigest()[:36]

        fw_txn = MagicMock()
        fw_txn.account_id = self.fw_acc_id
        fw_txn.import_id = original_id
        fw_txn.date = date(2023, 1, 1)
        fw_txn.amount = -500
        fw_txn.payee_name = "Grocery Store"
        fw_txn.category_id = None
        fw_txn.ynab_id = None

        ynab_txn = MagicMock()
        ynab_txn.import_id = hashed_id
        ynab_txn.id = "ynab_txn_id"
        ynab_txn.transfer_account_id = None
        ynab_txn.category_id = "some_category_id"  # Already categorized
        ynab_txn.deleted = False
        ynab_txn.var_date = date(2023, 1, 1)
        ynab_txn.amount = -500
        ynab_txn.payee_name = "Grocery Store"

        result = merge_and_filter_transactions([fw_txn], [ynab_txn], self.store)

        # Already categorized — should be skipped entirely
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()
