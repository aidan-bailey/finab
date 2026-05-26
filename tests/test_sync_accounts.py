import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore


class TestSyncAccounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_account(self, id, name, type="depository", balance=100000):
        a = MagicMock()
        a.id = id
        a.name = name
        a.type = type
        a.balance = balance
        record = {"id": id, "name": name, "type": type, "balance": balance}
        a.dict.return_value = record
        a.model_dump.return_value = record
        a.to_dict.return_value = record
        return a

    def _ynab_account(self, id, name):
        a = MagicMock()
        a.id = id
        a.ynab_id = id
        a.name = name
        record = {"id": id, "name": name}
        a.to_dict.return_value = record
        a.model_dump.return_value = record
        a.dict.return_value = record
        return a

    @patch("finab.main.input", create=True, return_value="My Checking")
    def test_skips_already_linked_accounts(self, _input):
        # Pre-populate the store
        self.store.add_account(
            alias="My Checking",
            fw_record={"id": "fw-1", "name": "Checking"},
            ynab_record={"id": "yn-1", "name": "My Checking"},
        )
        # Reload to pick up indexes
        self.store = ConfigStore(self.config_path)

        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = [self._ynab_account("yn-1", "My Checking")]

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        # No new account created via API
        self.ynab_client.create_account.assert_not_called()

    @patch("finab.main.input", create=True, return_value="My Checking")
    def test_links_existing_ynab_when_name_matches_alias(self, _input):
        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = [self._ynab_account("yn-1", "My Checking")]
        self.fw_client.get_transactions.return_value = []  # for balance adjustment

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_account.assert_not_called()
        linked = self.store.account_by_finwise_id("fw-1")
        self.assertIsNotNone(linked)
        self.assertEqual(linked["alias"], "My Checking")
        self.assertEqual(linked["ynab"]["id"], "yn-1")

    @patch("finab.main.input", create=True, return_value="Brand New Account")
    def test_creates_ynab_account_when_no_match(self, _input):
        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = []  # nothing on YNAB side
        self.fw_client.get_transactions.return_value = []
        created = self._ynab_account("yn-NEW", "Brand New Account")
        self.ynab_client.create_account.return_value = MagicMock(data=MagicMock(account=created))

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_account.assert_called_once()
        linked = self.store.account_by_finwise_id("fw-1")
        self.assertIsNotNone(linked)
        self.assertEqual(linked["alias"], "Brand New Account")
