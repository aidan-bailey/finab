import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore


class TestExtractDistinctMerchants(unittest.TestCase):
    def test_dedupes_by_merchant_id(self):
        from finab.main import _extract_distinct_merchants

        def fw_txn(mid, mname):
            t = MagicMock()
            t.merchant_id = mid
            t.merchant_name = mname
            return t

        txns = [
            fw_txn("m-1", "Spar"),
            fw_txn("m-1", "Spar"),
            fw_txn("m-2", "Checkers"),
            fw_txn(None, None),  # transactions with no merchant_id are skipped
        ]

        result = _extract_distinct_merchants(txns)
        ids = [m["id"] for m in result]
        self.assertEqual(sorted(ids), ["m-1", "m-2"])
        names = {m["id"]: m["name"] for m in result}
        self.assertEqual(names, {"m-1": "Spar", "m-2": "Checkers"})


class TestSyncMerchants(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, mid, mname):
        t = MagicMock()
        t.merchant_id = mid
        t.merchant_name = mname
        return t

    def _ynab_payee(self, id, name):
        p = MagicMock()
        p.id = id
        p.name = name
        p.to_dict.return_value = {"id": id, "name": name}
        p.model_dump.return_value = {"id": id, "name": name}
        return p

    @patch("finab.main.input", create=True, return_value="Spar")
    def test_attaches_second_finwise_to_existing_merchant(self, _input):
        # Existing merchant with one FinWise child
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar-1", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.config_path)

        # Transaction has a NEW FinWise merchant id with same alias-typed name
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-spar-2", "Spar"),
        ]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-spar", "Spar")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        # The new FinWise id is attached to the same merchant
        m = self.store.merchant_by_finwise_id("fw-spar-2")
        self.assertIsNotNone(m)
        self.assertEqual(m["alias"], "Spar")
        self.assertIn("fw-spar-1", m["finwise"])
        self.assertIn("fw-spar-2", m["finwise"])
        # No new YNAB payee created
        self.ynab_client.create_payee.assert_not_called()

    @patch("finab.main.input", create=True, return_value="New Payee")
    def test_creates_ynab_payee_when_no_match(self, _input):
        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-x", "X")]
        self.ynab_client.get_payees.return_value = []
        created = self._ynab_payee("yn-new", "New Payee")
        self.ynab_client.create_payee.return_value = created

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_payee.assert_called_once_with("bid", "New Payee")
        m = self.store.merchant_by_finwise_id("fw-x")
        self.assertEqual(m["alias"], "New Payee")
        self.assertEqual(m["ynab"]["id"], "yn-new")

    @patch("finab.main.input", create=True, return_value="Shell")
    def test_links_existing_ynab_payee_when_name_matches(self, _input):
        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-shell", "Shell")]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-shell", "Shell")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_payee.assert_not_called()
        m = self.store.merchant_by_finwise_id("fw-shell")
        self.assertEqual(m["ynab"]["id"], "yn-shell")

    @patch("finab.main.input", create=True, return_value="Spar")
    def test_skip_known_merchant(self, _input):
        # Already linked
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.config_path)

        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-spar", "Spar")]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-spar", "Spar")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        # No prompt should fire (mock_input would still have returned "Spar", but
        # we verify behavior by checking that create_payee wasn't called and the
        # merchant still has exactly one finwise child).
        self.ynab_client.create_payee.assert_not_called()
        m = self.store.merchant_by_finwise_id("fw-spar")
        self.assertEqual(len(m["finwise"]), 1)


class TestRecordMerchantAliasFallback(unittest.TestCase):
    """Defensive fallback path: _record_merchant_alias should fully link to
    a YNAB payee, not leave an empty ynab={} placeholder."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_creates_full_merchant_when_no_match(self):
        from finab.main import _record_merchant_alias
        self.ynab_client.get_payees.return_value = []
        created = MagicMock(id="yn-new", name="Foo")
        created.to_dict.return_value = {"id": "yn-new", "name": "Foo"}
        created.model_dump.return_value = {"id": "yn-new", "name": "Foo"}
        self.ynab_client.create_payee.return_value = created

        _record_merchant_alias(
            self.store,
            self.ynab_client,
            "bid",
            fw_merchant_id="fw-foo",
            alias="Foo",
            fw_merchant_name="Foo",
        )

        m = self.store.merchant_by_finwise_id("fw-foo")
        self.assertIsNotNone(m)
        self.assertEqual(m["ynab"], {"id": "yn-new", "name": "Foo"})
        self.ynab_client.create_payee.assert_called_once_with("bid", "Foo")

    def test_attaches_to_existing_merchant_by_alias(self):
        from finab.main import _record_merchant_alias
        self.store.add_merchant(
            alias="Shell",
            fw_record={"id": "fw-shell-1", "name": "Shell"},
            ynab_record={"id": "yn-shell", "name": "Shell"},
        )
        self.store = ConfigStore(self.config_path)

        _record_merchant_alias(
            self.store,
            self.ynab_client,
            "bid",
            fw_merchant_id="fw-shell-2",
            alias="Shell",
            fw_merchant_name="Shell",
        )

        self.ynab_client.create_payee.assert_not_called()
        m = self.store.merchant_by_finwise_id("fw-shell-2")
        self.assertIn("fw-shell-1", m["finwise"])
        self.assertIn("fw-shell-2", m["finwise"])
