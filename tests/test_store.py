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

    def test_account_by_alias_normalizes_lookup(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="Discovery Bank ZAR",
            fw_record={"id": "fw-1", "name": "Discovery"},
            ynab_record={"id": "yn-1", "transfer_payee_id": "tp-1"},
        )

        self.assertIsNotNone(store.account_by_alias("Discovery Bank ZAR"))
        self.assertIsNotNone(store.account_by_alias("  discovery bank zar  "))
        self.assertIsNone(store.account_by_alias("Savings"))


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

        # Build a fake FinWise account matching the real internal Account
        # model's attribute shape (finwise_id, plus the curated fields).
        class FakeFW:
            finwise_id = "fw-A"
            name = "New Name"
            type = "checking"
            balance = 200
            currency_code = "ZAR"

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
            finwise_id = "fw-unknown"
            name = "Unknown"
            type = "checking"
            balance = 0
            currency_code = "ZAR"

        # Should not raise even though no account is linked
        store.refresh_records(fw_accounts=[FakeFW()])


class TestSetMerchantMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_set_merchant_memory_writes_both_fields(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        cu = {"cat-1": 5, "cat-2": 1}
        processings = {
            "-10000": {
                "parent_memo": "supermarket",
                "splits": [{"category_id": "cat-1",
                            "amount_milliunits": -10000, "memo": ""}],
            }
        }
        store.set_merchant_memory(m["id"], categories_used=cu, processings=processings)

        store2 = ConfigStore(self.path)
        found = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(found["categories_used"], cu)
        self.assertEqual(found["processings"], processings)


class TestMigrateLastProcessingToProcessings(unittest.TestCase):
    """One-shot migration: legacy merchant.last_processing becomes a
    single-entry merchant.processings on load via _rebuild_indexes."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_legacy_last_processing_migrates_to_processings(self):
        legacy = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "categories_used": {"cat-A": 3},
                    "last_processing": {
                        "amount_milliunits": -50000,
                        "parent_memo": "groceries",
                        "splits": [
                            {"category_id": "cat-A",
                             "amount_milliunits": -50000,
                             "memo": ""}
                        ],
                    },
                }
            },
        }
        self.path.write_text(json.dumps(legacy))

        store = ConfigStore(self.path)
        m = store._data["merchants"]["m-1"]

        self.assertNotIn("last_processing", m)
        self.assertIn("processings", m)
        self.assertIn("-50000", m["processings"])
        self.assertEqual(m["processings"]["-50000"]["parent_memo"], "groceries")
        self.assertEqual(
            m["processings"]["-50000"]["splits"][0]["category_id"], "cat-A"
        )

    def test_already_migrated_is_idempotent(self):
        already = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "processings": {
                        "-50000": {"parent_memo": "", "splits": []}
                    },
                }
            },
        }
        self.path.write_text(json.dumps(already))

        store = ConfigStore(self.path)
        m = store._data["merchants"]["m-1"]
        self.assertEqual(list(m["processings"].keys()), ["-50000"])
        self.assertNotIn("last_processing", m)

    def test_migration_persists_to_disk(self):
        legacy = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "last_processing": {
                        "amount_milliunits": -50000,
                        "parent_memo": "",
                        "splits": [],
                    },
                }
            },
        }
        self.path.write_text(json.dumps(legacy))

        ConfigStore(self.path)

        on_disk = json.loads(self.path.read_text())
        m = on_disk["merchants"]["m-1"]
        self.assertNotIn("last_processing", m)
        self.assertIn("processings", m)


if __name__ == "__main__":
    unittest.main()
