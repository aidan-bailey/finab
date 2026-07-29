"""Tests for FinWiseClient.get_transactions pagination."""
import json
from finab.client import FinWiseClient


class _FakeTransport:
    """Returns canned pages keyed by the requested pageNumber."""
    def __init__(self, pages):
        self._pages = pages          # {pageNumber: [raw_txn_dict, ...]}
        self.calls = []              # list of params dicts seen

    def get(self, path, *, params=None):
        self.calls.append(params)
        page = json.loads(params["pagination"])["pageNumber"]
        return self._pages.get(page, [])


def _raw(i):
    """Minimal raw FinWise transaction dict accepted by FinWiseTransaction."""
    return {
        "id": f"fw-{i}", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z", "description": f"txn {i}",
        "accountId": "acc-1", "amount": {"amount": 1, "currencyCode": "ZAR"},
        "date": "2026-01-01T00:00:00Z", "merchantId": "m-1",
        "userId": "u-1", "needsReview": False,
    }


def _client_with(pages):
    c = FinWiseClient.__new__(FinWiseClient)        # bypass __init__/network
    class _Inner: pass
    c._client = _Inner()
    c._client._transport = _FakeTransport(pages)
    return c


def test_get_transactions_paginates_until_short_page():
    # 500 on page 1 (full), 3 on page 2 (short → stop).
    pages = {1: [_raw(i) for i in range(500)], 2: [_raw(i) for i in range(500, 503)]}
    c = _client_with(pages)
    txns = c.get_transactions()
    assert len(txns) == 503
    # Two page requests, JSON-encoded pagination param.
    assert len(c._client._transport.calls) == 2
    assert json.loads(c._client._transport.calls[0]["pagination"]) == {"pageNumber": 1, "pageSize": 500}
    assert json.loads(c._client._transport.calls[1]["pagination"]) == {"pageNumber": 2, "pageSize": 500}


def test_get_transactions_single_short_page_stops_immediately():
    c = _client_with({1: [_raw(i) for i in range(10)]})
    txns = c.get_transactions()
    assert len(txns) == 10
    assert len(c._client._transport.calls) == 1


class _MerchTransport:
    """Returns a canned /merchants list, ignores other paths."""
    def __init__(self, merchants):
        self._m = merchants
        self.paths = []

    def get(self, path, *, params=None):
        self.paths.append(path)
        return self._m if path == "/merchants" else []


def _client_with_merchants(merchants):
    c = FinWiseClient.__new__(FinWiseClient)
    class _Inner: pass
    c._client = _Inner()
    c._client._transport = _MerchTransport(merchants)
    return c


def test_get_merchants_returns_id_to_name_map():
    c = _client_with_merchants([
        {"id": "m-1", "name": "Total"},
        {"id": "m-2", "name": "Woolworths"},
    ])
    assert c.get_merchants() == {"m-1": "Total", "m-2": "Woolworths"}
    assert c._client._transport.paths == ["/merchants"]


def test_get_merchants_skips_records_without_id():
    c = _client_with_merchants([
        {"id": "m-1", "name": "Total"},
        {"name": "orphan with no id"},
    ])
    assert c.get_merchants() == {"m-1": "Total"}
