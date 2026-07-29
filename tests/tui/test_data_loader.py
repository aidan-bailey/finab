"""Tests for the async data loader."""
import pytest
from finab.tui.data_loader import LoadedData, load_all


class _FakeFwClient:
    def __init__(self, accounts=None, transactions=None, merchants=None, raise_on=None):
        self._accounts = accounts or []
        self._transactions = transactions or []
        self._merchants = merchants if merchants is not None else {}
        self._raise_on = raise_on
    def get_accounts(self):
        if self._raise_on == "accounts":
            raise RuntimeError("fw accounts fetch failed")
        return self._accounts
    def get_transactions(self, **kwargs):
        if self._raise_on == "transactions":
            raise RuntimeError("fw transactions fetch failed")
        return self._transactions
    def get_merchants(self):
        if self._raise_on == "merchants":
            raise RuntimeError("fw merchants fetch failed")
        return self._merchants


class _Txn:
    """Minimal stand-in for the unified Transaction (mutable merchant_name)."""
    def __init__(self, merchant_id, merchant_name=None):
        self.merchant_id = merchant_id
        self.merchant_name = merchant_name


class _FakeYnabClient:
    def __init__(self, accounts=None, transactions=None, categories=None, category_groups=None, payees=None, raise_on=None):
        self._accounts = accounts or []
        self._transactions = transactions or []
        self._categories = categories or []
        self._category_groups = category_groups or []
        self._payees = payees or []
        self._raise_on = raise_on
    def get_accounts(self, budget_id):
        if self._raise_on == "ynab_accounts":
            raise RuntimeError("ynab accounts fetch failed")
        return self._accounts
    def get_transactions(self, budget_id):
        if self._raise_on == "ynab_transactions":
            raise RuntimeError("ynab transactions fetch failed")
        return self._transactions
    def get_categories(self, budget_id):
        return self._categories
    def get_category_groups_with_categories(self, budget_id):
        return self._category_groups
    def get_payees(self, budget_id):
        return self._payees


async def test_load_all_returns_loaded_data():
    fw = _FakeFwClient(accounts=["fw-acc-1"], transactions=["fw-txn-1"])
    ynab = _FakeYnabClient(
        accounts=["yn-acc-1"],
        transactions=["yn-txn-1"],
        categories=["cat-1"],
        category_groups=["cg-1"],
        payees=["payee-1"],
    )
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert isinstance(data, LoadedData)
    assert data.fw_accounts == ["fw-acc-1"]
    assert data.fw_transactions == ["fw-txn-1"]
    assert data.ynab_accounts == ["yn-acc-1"]
    assert data.ynab_transactions == ["yn-txn-1"]
    assert data.ynab_categories == ["cat-1"]
    assert data.ynab_category_groups == ["cg-1"]
    assert data.ynab_payees == ["payee-1"]
    assert data.error is None


async def test_load_all_captures_exception():
    fw = _FakeFwClient(raise_on="transactions")
    ynab = _FakeYnabClient()
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert data.error is not None
    assert "fw transactions fetch failed" in str(data.error)


async def test_load_all_resolves_merchant_names():
    """merchant_name is backfilled from /merchants where null, never
    overwriting an existing name, and ignoring txns without a merchant_id."""
    txns = [_Txn("m-1"), _Txn("m-2", "Existing"), _Txn(None)]
    fw = _FakeFwClient(transactions=txns, merchants={"m-1": "Total", "m-2": "Woolworths"})
    ynab = _FakeYnabClient()
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert data.error is None
    assert txns[0].merchant_name == "Total"       # filled from map
    assert txns[1].merchant_name == "Existing"     # not overwritten
    assert txns[2].merchant_name is None           # no merchant_id → skipped
    assert data.fw_merchants == {"m-1": "Total", "m-2": "Woolworths"}


async def test_load_all_merchant_name_failure_is_non_fatal():
    """A /merchants failure must not break the whole load — names are a
    display nicety, not load-critical."""
    txns = [_Txn("m-1")]
    fw = _FakeFwClient(transactions=txns, raise_on="merchants")
    ynab = _FakeYnabClient()
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert data.error is None
    assert txns[0].merchant_name is None
