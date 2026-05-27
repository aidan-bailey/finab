"""Smoke tests for finab.engine.merchants. See test_accounts_helpers.py
for the pattern — these only confirm the new import path; detailed
behaviour tests live in test_sync_merchants.py.
"""


def test_helpers_importable_from_engine_merchants():
    from finab.engine.merchants import (
        _link_account_transfer_payee,
        _extract_distinct_merchants,
        _reconcile_store_merchants_to_ynab,
        _record_merchant_alias,
    )
    assert callable(_link_account_transfer_payee)
    assert callable(_extract_distinct_merchants)
    assert callable(_reconcile_store_merchants_to_ynab)
    assert callable(_record_merchant_alias)


def test_helpers_still_importable_from_main():
    from finab.main import (
        _link_account_transfer_payee,
        _extract_distinct_merchants,
        _reconcile_store_merchants_to_ynab,
        _record_merchant_alias,
    )
    assert callable(_link_account_transfer_payee)
    assert callable(_extract_distinct_merchants)
    assert callable(_reconcile_store_merchants_to_ynab)
    assert callable(_record_merchant_alias)
