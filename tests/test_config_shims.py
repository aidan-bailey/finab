import unittest
import tempfile
from pathlib import Path

from finab.config import load_aliases, load_merchant_aliases
from finab.store import ConfigStore


class TestConfigShims(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_aliases_synthesizes_from_store(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="Checking",
            fw_record={"id": "fw-1", "name": "FinWise Checking"},
            ynab_record={"id": "yn-1", "name": "Checking"},
        )
        store.add_account(
            alias="Savings",
            fw_record={"id": "fw-2", "name": "FinWise Savings"},
            ynab_record={"id": "yn-2", "name": "Savings"},
        )

        aliases = load_aliases(store=store)
        self.assertEqual(
            aliases,
            {"FinWise Checking": "Checking", "FinWise Savings": "Savings"},
        )

    def test_load_merchant_aliases_flattens_one_to_many(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Easy Equities",
            fw_record={"id": "fw-m-A", "name": "EE"},
            ynab_record={"id": "yn-p-EE", "name": "Easy Equities"},
        )
        store.attach_finwise_to_merchant(m["id"], {"id": "fw-m-B", "name": "EE"})

        aliases = load_merchant_aliases(store=store)
        self.assertEqual(
            aliases,
            {"fw-m-A": "Easy Equities", "fw-m-B": "Easy Equities"},
        )

    def test_save_aliases_no_longer_exists(self):
        import finab.config as config_module
        self.assertFalse(hasattr(config_module, "save_aliases"))
        self.assertFalse(hasattr(config_module, "save_merchant_aliases"))


if __name__ == "__main__":
    unittest.main()
