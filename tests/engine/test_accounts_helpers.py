"""Smoke tests for the helpers extracted into finab.engine.accounts.

Detailed behaviour tests for these functions live in the existing
tests/test_sync_accounts.py — they import via finab.main, which
re-exports from engine/accounts.py. This file locks in the new
import location.
"""


def test_helpers_importable_from_engine_accounts():
    from finab.engine.accounts import (
        _calculate_starting_balance,
        _account_with_overrides,
        _reconcile_store_accounts_to_ynab,
    )
    # If we got here, the names are exported.
    assert callable(_calculate_starting_balance)
    assert callable(_account_with_overrides)
    assert callable(_reconcile_store_accounts_to_ynab)


def test_helpers_still_importable_from_main():
    from finab.main import (
        _calculate_starting_balance,
        _account_with_overrides,
        _reconcile_store_accounts_to_ynab,
    )
    assert callable(_calculate_starting_balance)
