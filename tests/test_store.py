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


class TestConfigStoreMerchants(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_merchant_keeps_finwise_as_dict_keyed_by_fw_id(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-m-1", "name": "Spar"}
        yn = {"id": "yn-p-1", "name": "Spar"}

        m = store.add_merchant(alias="Spar", fw_record=fw, ynab_record=yn)

        self.assertEqual(m["alias"], "Spar")
        self.assertEqual(m["finwise"], {"fw-m-1": fw})
        self.assertEqual(m["ynab"], yn)

    def test_merchant_by_finwise_id_finds_any_finwise_child(self):
        store = ConfigStore(self.path)
        fw_a = {"id": "fw-m-A", "name": "Easy Equities"}
        fw_b = {"id": "fw-m-B", "name": "Easy Equities"}
        yn = {"id": "yn-p-EE", "name": "Easy Equities"}

        m = store.add_merchant(alias="Easy Equities", fw_record=fw_a, ynab_record=yn)
        store.attach_finwise_to_merchant(m["id"], fw_b)

        self.assertEqual(store.merchant_by_finwise_id("fw-m-A")["id"], m["id"])
        self.assertEqual(store.merchant_by_finwise_id("fw-m-B")["id"], m["id"])

    def test_merchant_by_alias_normalizes_lookup(self):
        store = ConfigStore(self.path)
        store.add_merchant(
            alias="Pick n Pay",
            fw_record={"id": "fw-pnp", "name": "PnP"},
            ynab_record={"id": "yn-pnp", "name": "Pick n Pay"},
        )

        # Exact match
        self.assertIsNotNone(store.merchant_by_alias("Pick n Pay"))
        # Lowercased + whitespace tolerant
        self.assertIsNotNone(store.merchant_by_alias("  pick n pay  "))
        # Different alias misses
        self.assertIsNone(store.merchant_by_alias("Checkers"))

    def test_attach_finwise_to_merchant_persists(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Shell",
            fw_record={"id": "fw-shell-1", "name": "Shell"},
            ynab_record={"id": "yn-shell", "name": "Shell"},
        )
        store.attach_finwise_to_merchant(m["id"], {"id": "fw-shell-2", "name": "Shell"})

        # Reload from disk to ensure persistence
        store2 = ConfigStore(self.path)
        m2 = store2.merchant_by_finwise_id("fw-shell-2")
        self.assertEqual(m2["id"], m["id"])
        self.assertIn("fw-shell-1", m2["finwise"])
        self.assertIn("fw-shell-2", m2["finwise"])


class TestRefreshRecords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_refresh_overwrites_finwise_record(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="A",
            fw_record={"id": "fw-A", "name": "Old Name", "balance": 100},
            ynab_record={"id": "yn-A", "name": "A"},
        )

        # Build a fake FinWise account with updated fields
        class FakeFW:
            id = "fw-A"
            def dict(self):
                return {"id": "fw-A", "name": "New Name", "balance": 200}

        store.refresh_records(fw_accounts=[FakeFW()])

        acc = store.account_by_finwise_id("fw-A")
        self.assertEqual(acc["finwise"]["name"], "New Name")
        self.assertEqual(acc["finwise"]["balance"], 200)

    def test_refresh_overwrites_ynab_payee_on_merchant(self):
        store = ConfigStore(self.path)
        store.add_merchant(
            alias="Shell",
            fw_record={"id": "fw-shell", "name": "Shell"},
            ynab_record={"id": "yn-shell", "name": "Shell (old)"},
        )

        class FakeYNAB:
            id = "yn-shell"
            def to_dict(self):
                return {"id": "yn-shell", "name": "Shell"}

        store.refresh_records(ynab_payees=[FakeYNAB()])

        m = store.merchant_by_finwise_id("fw-shell")
        self.assertEqual(m["ynab"]["name"], "Shell")

    def test_refresh_ignores_unknown_records(self):
        store = ConfigStore(self.path)

        class FakeFW:
            id = "fw-unknown"
            def dict(self):
                return {"id": "fw-unknown"}

        # Should not raise even though no account is linked
        store.refresh_records(fw_accounts=[FakeFW()])


if __name__ == "__main__":
    unittest.main()
