# `ignore_transactions` Flag — Design

**Status:** Approved (design phase)
**Date:** 2026-05-27
**Scope:** Add an optional `ignore_transactions: true` boolean field on account entries in `config.json`. Phase 2 (merchant extraction) and Phase 3 (transaction sync) both skip transactions whose FinWise account carries the flag. No UI for setting the flag — user edits `config.json` manually.

## Motivation

FinWise exposes some accounts as aggregator parents whose transactions are duplicated under both the parent and the real sub-account. Concrete example: "Discovery Bank ZAR" is a parent / virtual account that holds no money, and every transaction in "Discovery Bank Transaction Account" appears under both. Without filtering, Phase 3 creates duplicates in YNAB because each FinWise transaction has a unique id under each account (so the local sync map can't dedup them).

A per-account flag lets the user mark Discovery Bank ZAR (or any other aggregator) as "don't sync transactions from this account." Phase 1 still keeps the account in the store (so it isn't re-prompted on every run); Phase 2 and Phase 3 just skip the transactions originating from it.

## Schema

```json
"accounts": {
  "<uuid>": {
    "id": "<uuid>",
    "alias": "Discovery Bank ZAR",
    "finwise": { ... },
    "ynab":    { ... },
    "ignore_transactions": true
  }
}
```

- **Optional field.** Missing means false. `acc.get("ignore_transactions")` returns `None` (falsy) and the account behaves normally.
- **No migration needed.** Existing entries don't carry the field; the read code defaults to false.
- **Manual edit.** The user opens `config.json` and adds `"ignore_transactions": true` to the relevant account entry once per ignored account. No CLI prompt for now.

## Behaviour Changes

### Phase 3 — `merge_and_filter_transactions`

In `src/finab/transactions.py`:

```python
for fw_txn in fw_transactions:
    acc = store.account_by_finwise_id(fw_txn.account_id)
    if not acc:
        continue
    if acc.get("ignore_transactions"):
        continue              # NEW
    ynab_account_id = acc["ynab"].get("id")
    ...
```

Transactions whose FinWise account is flagged are dropped before they reach the dedup / categorization flow.

### Phase 2 — `sync_merchants`

In `src/finab/main.py` (where `sync_merchants` lives — around line 464), filter the FinWise transactions list before extracting distinct merchants:

```python
fw_transactions = fw_client.get_transactions()

# Drop transactions from FW accounts flagged to ignore.
def _account_is_ignored(fw_account_id: str) -> bool:
    acc = store.account_by_finwise_id(fw_account_id)
    return bool(acc and acc.get("ignore_transactions"))

fw_transactions = [
    t for t in fw_transactions
    if not _account_is_ignored(t.account_id)
]

fw_merchants = _extract_distinct_merchants(fw_transactions)
...
```

`_extract_distinct_merchants` itself is unchanged — the filter happens at the call site so the helper stays simple and store-agnostic.

### Schema visibility — `Phase 1`

`sync_accounts` is unchanged. The flag is not set or referenced during account sync. Adding/removing the flag is the user's responsibility. The flag survives Phase 1's `refresh_records` because that method only overwrites the `finwise` and `ynab` sub-records — not other fields on the account entry.

## Tests

Two new tests, one per filter point.

### `tests/test_transactions.py::TestMergeAndFilter` — Phase 3 filter

```python
def test_skips_account_marked_ignore_transactions(self):
    """Transactions whose FW account is flagged ignore_transactions are
    dropped — they never reach the YNAB sync."""
    # Mark the seeded "Checking" account as ignore_transactions=True.
    accounts = list(self.store.accounts())
    self.store._data["accounts"][accounts[0]["id"]]["ignore_transactions"] = True
    self.store._save()
    self.store = ConfigStore(self.config_path)

    fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
    result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
    self.assertEqual(result, [])
```

### `tests/test_sync_merchants.py` (or wherever Phase 2 tests live) — Phase 2 filter

```python
def test_skips_merchants_from_ignored_accounts(self):
    """Phase 2's merchant extraction skips transactions whose FW account
    is flagged ignore_transactions."""
    # Existing accounts/merchants fixture setup ...
    # Mark the account as ignored.
    accounts = list(self.store.accounts())
    self.store._data["accounts"][accounts[0]["id"]]["ignore_transactions"] = True
    self.store._save()
    self.store = ConfigStore(self.config_path)

    # FW transactions point at the ignored account.
    self.fw_client.get_transactions.return_value = [
        self._fw_txn("fw-ignored-acc", "fw-merchant-A"),
    ]
    self.ynab_client.get_payees.return_value = []

    sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

    # No merchant should be added — the transactions were filtered out.
    self.assertIsNone(self.store.merchant_by_finwise_id("fw-merchant-A"))
```

Existing tests don't set the flag and continue to pass — the new branch is a no-op for unflagged accounts.

## File Changes Summary

| File | Change |
|---|---|
| `src/finab/transactions.py` | Add 2-line skip in `merge_and_filter_transactions` (Phase 3 path). |
| `src/finab/main.py` | Filter the `fw_transactions` list in `sync_merchants` before calling `_extract_distinct_merchants`. |
| `tests/test_transactions.py` | One new test in `TestMergeAndFilter`. |
| `tests/test_sync_merchants.py` | One new test for Phase 2 filter. |

`ConfigStore` API is unchanged. No migration. No new methods.

## Non-Goals

- **CLI prompt for setting the flag.** Manual `config.json` edit only. If many aggregator-parent accounts ever exist, a Phase 1 prompt is a separable future feature.
- **Heuristic detection of aggregator accounts.** No magic auto-detection. Explicit user intent only.
- **Once-off cleanup of duplicate YNAB transactions already created.** User has already cleaned these up manually. Future runs are protected by the filter.
- **Removing ignored accounts from the store.** The account entry stays — only its transactions are filtered out. This way Phase 1's prelude doesn't re-prompt for an account the user has explicitly chosen to keep but ignore.
