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


if __name__ == "__main__":
    unittest.main()
