import json
import unittest
from pathlib import Path
import tempfile

from finab.store import ConfigStore


class TestConfigStoreBasics(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_missing_file_returns_empty_store(self):
        store = ConfigStore(self.path)
        self.assertEqual(list(store.accounts()), [])
        self.assertEqual(list(store.merchants()), [])

    def test_load_preserves_unrelated_keys(self):
        self.path.write_text(json.dumps({
            "budget_id": "abc",
            "payee_rules": [{"pattern": "x", "target": "y"}],
            "accounts": {},
            "merchants": {},
        }))
        store = ConfigStore(self.path)
        # Unrelated keys round-trip through the data dict
        self.assertEqual(store._data["budget_id"], "abc")
        self.assertEqual(store._data["payee_rules"], [{"pattern": "x", "target": "y"}])

    def test_atomic_save_writes_via_tmp(self):
        store = ConfigStore(self.path)
        store._data["sentinel"] = "value"
        store._save()
        self.assertTrue(self.path.exists())
        with open(self.path) as f:
            self.assertEqual(json.load(f)["sentinel"], "value")
        # No leftover .tmp file
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())


class TestConfigStoreAccounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_account_creates_uuid_and_persists(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-1", "name": "Checking"}
        yn = {"id": "yn-1", "name": "Checking"}

        acc = store.add_account(alias="Checking", fw_record=fw, ynab_record=yn)

        self.assertEqual(acc["alias"], "Checking")
        self.assertEqual(acc["finwise"], fw)
        self.assertEqual(acc["ynab"], yn)
        self.assertTrue(acc["id"])

        # Reload from disk: data round-trips
        store2 = ConfigStore(self.path)
        self.assertEqual(list(store2.accounts()), [acc])

    def test_account_by_finwise_id_lookup(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-7", "name": "Savings"}
        yn = {"id": "yn-7", "name": "Savings"}
        acc = store.add_account(alias="Savings", fw_record=fw, ynab_record=yn)

        self.assertEqual(store.account_by_finwise_id("fw-7"), acc)
        self.assertIsNone(store.account_by_finwise_id("fw-missing"))

    def test_indexes_rebuild_after_add(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="A", fw_record={"id": "fw-A", "name": "A"}, ynab_record={"id": "yn-A"}
        )
        store.add_account(
            alias="B", fw_record={"id": "fw-B", "name": "B"}, ynab_record={"id": "yn-B"}
        )

        self.assertIn("fw-A", store._fw_account_index)
        self.assertIn("fw-B", store._fw_account_index)
        self.assertNotEqual(
            store._fw_account_index["fw-A"], store._fw_account_index["fw-B"]
        )


if __name__ == "__main__":
    unittest.main()
