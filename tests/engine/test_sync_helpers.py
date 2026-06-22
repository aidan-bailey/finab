"""Smoke tests confirming engine/sync.py exposes the helpers we moved.

The detailed behaviour tests for these helpers live in
tests/test_transactions.py — they import via finab.transactions and
exercise the re-exported names. This file just locks in the new
public import location.
"""
import pytest

def test_helpers_importable_from_engine_sync():
    from finab.engine.sync import (
        _INFLOW_CATEGORY_NAMES,
        _TRACKING_ACCOUNT_TYPES,
        _account_is_tracking,
        _is_inflow,
        _is_before_current_month,
        _is_transfer,
        _find_inflow_category,
        _closest_processing,
        _apply_repeat,
        _apply_processing_to_txn,
        _update_merchant_memory,
        _category_name,
        _render_splits,
        _sort_key,
        merge_and_filter_transactions,
    )
    # If we got here, all names are exported.
    assert _INFLOW_CATEGORY_NAMES  # constant is non-empty
    assert _TRACKING_ACCOUNT_TYPES  # constant is non-empty


def test_helpers_still_importable_from_transactions():
    """Existing call sites import these from finab.transactions; that must keep working."""
    from finab.transactions import (
        _INFLOW_CATEGORY_NAMES,
        _TRACKING_ACCOUNT_TYPES,
        _account_is_tracking,
        _is_inflow,
        _is_before_current_month,
        _is_transfer,
        _find_inflow_category,
        _closest_processing,
        _apply_repeat,
        _apply_processing_to_txn,
        _update_merchant_memory,
        _category_name,
        _render_splits,
        _sort_key,
        merge_and_filter_transactions,
    )
    assert _INFLOW_CATEGORY_NAMES
    assert _TRACKING_ACCOUNT_TYPES


def test_from_finwise_sets_fw_uuid_to_source_id():
    from finab.models import FinWiseTransaction, Transaction

    raw = {
        "id": "fw-abc", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z", "description": "x",
        "accountId": "acc-1", "amount": {"amount": 5, "currencyCode": "ZAR"},
        "date": "2026-01-01T00:00:00Z", "merchantId": "m-1",
        "userId": "u-1", "needsReview": False,
    }
    txn = Transaction.from_finwise(FinWiseTransaction.model_validate(raw))
    assert txn.fw_uuid == "fw-abc"
    assert txn.import_id == "fw-abc"   # unchanged: still seeds import_id too
