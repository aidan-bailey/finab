# Phase 3 follow-ups: ignore_transactions flag, stable import_id, skip-all, fail-loud

**Status:** Approved (design phase)
**Date:** 2026-05-27
**Scope:** Four independent improvements to Phase 2 / Phase 3, bundled because they all surfaced from one production run:
  1. **`ignore_transactions` flag** on account entries — skip transactions from aggregator-parent FinWise accounts (e.g., "Discovery Bank ZAR").
  2. **Stable import_id** stored in `transactions.json` — our durable dedup key, sent to YNAB as `import_id`, rotated only when the YNAB twin disappears.
  3. **`q` to skip-all** in the categorization prompt — break the loop, auto-flush, finish Phase 3.
  4. **Fail loud** — let exceptions propagate instead of swallowing them; a previous run silently exited after a flush failure with no traceback.

## Motivation

All four improvements came out of a single Phase 3 production run:

1. **Discovery Bank ZAR was duplicating transactions.** FinWise reports each Transaction-Account transaction TWICE — once under the real sub-account and once under "Discovery Bank ZAR", which is a parent / virtual account that holds no money. Without filtering, Phase 3 created two YNAB transactions per real transaction because the FinWise transaction ids differed across accounts so the local sync map couldn't dedup them.

2. **Transient correlator import_ids made YNAB records hard to inspect.** The current Phase 3 design generates a fresh random UUID per push as a transport-layer correlator. Every re-sync of the same transaction (e.g. categorizing a previously-pushed uncategorized YNAB transaction) imprints a different `import_id` on the YNAB record. A durable UUID that we own — stored in `transactions.json` and used as both the YNAB `import_id` field and our dedup key — is simpler, more debuggable, and survives a YNAB-side delete via UUID rotation.

3. **Categorization is all-or-nothing.** You either finish every transaction the run pulled, or Ctrl+C kills the loop. There's no clean "I'm done for now, push what I have" option from inside the prompt.

4. **Silent failure on flush.** After categorizing N transactions, the flush hit an exception, `_PendingQueue.flush` caught it, printed "Flush failed: ..." once, returned False, and the orchestrator continued (auto-flush re-tried at end of `finally`, also failed). The run exited 0. Nothing in YNAB. No traceback. The user had no idea what broke.

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

## Stable import_id

### Schema

`transactions.json` changes meaning: it now maps `fw_uuid` to **our** durable import_id (a random uuid4 hex), not to the YNAB transaction id. The YNAB id is rediscovered each run by matching `import_id` against YNAB's response.

Before:
```json
{ "synced_transactions": { "<fw_uuid>": "<ynab_uuid>" } }
```

After:
```json
{ "synced_transactions": { "<fw_uuid>": "<our_import_id>" } }
```

### Dedup flow in `merge_and_filter_transactions`

```python
ynab_by_import_id = {
    str(t.import_id): t for t in ynab_transactions
    if not getattr(t, "deleted", False) and getattr(t, "import_id", None)
}

# Prune entries whose import_id is no longer in the live YNAB fetch.
tx_store.prune_stale(set(ynab_by_import_id.keys()))

for fw_txn in fw_transactions:
    acc = store.account_by_finwise_id(fw_txn.account_id)
    if not acc:
        continue
    if acc.get("ignore_transactions"):
        continue
    ynab_account_id = acc["ynab"].get("id")
    if not ynab_account_id:
        continue

    fw_uuid = fw_txn.import_id  # FW's own UUID, set by from_finwise
    our_id = tx_store.import_id_for(fw_uuid) if fw_uuid else None

    if our_id and our_id in ynab_by_import_id:
        # Already synced; YNAB still has it.
        ynab_match = ynab_by_import_id[our_id]
        if getattr(ynab_match, "category_id", None):
            continue  # already categorized — preserve manual YNAB edits
        fw_txn.ynab_id = str(ynab_match.id)
        fw_txn.import_id = our_id  # keep stable for update push
    else:
        # Either never synced, or YNAB-twin missing (user deleted in YNAB).
        # Rotate: generate fresh UUID, overwrite stored, push as new.
        new_id = uuid.uuid4().hex
        tx_store.record(fw_uuid, new_id)
        fw_txn.import_id = new_id

    fw_txn.account_id = ynab_account_id
    out.append(fw_txn)
```

### `_PendingQueue.flush` simplifies

The transient-correlator logic disappears. `flush` just sends `txn.import_id` (already set by `merge_and_filter`) and returns; no post-flush `record_syncs` call, no `correlation_map`. The store's mapping was already written in `merge_and_filter`.

```python
def flush(self, ynab_client: YNABClient, budget_id: str) -> bool:
    """Push all pending transactions in two batched calls. Returns True
    on success. Raises on failure (no swallowing)."""
    creates_snap = list(self.creates)
    updates_snap = list(self.updates)
    if creates_snap:
        ynab_client.create_transactions(budget_id, creates_snap)
    if updates_snap:
        ynab_client.update_transactions(budget_id, updates_snap)
    self.creates.clear()
    self.updates.clear()
    return True
```

(Note: no `try/except` — this is the fail-loud change for flush. See [Fail Loud](#fail-loud) below.)

### TransactionsStore API renames

| Before | After |
|---|---|
| `synced_ynab_id(fw_uuid)` | `import_id_for(fw_uuid)` |
| `record_sync(fw_uuid, ynab_uuid)` | `record(fw_uuid, import_id)` |
| `record_syncs(mappings)` | (deleted — flush no longer batches mappings) |
| `prune_stale(live_ynab_ids)` | `prune_stale(live_import_ids)` (semantically: set of import_ids, not YNAB UUIDs) |
| `remove_sync(fw_uuid)` | `remove(fw_uuid)` |

### Migration

Hard cutoff. Existing `transactions.json` entries map fw → ynab_uuid (legacy semantics). After this change, the same dict keys are used but values are interpreted as import_ids, which YNAB won't have any record of. So on first run:

- `merge_and_filter` treats every FW transaction as "needs rotation" (because the stored value isn't an import_id YNAB knows about).
- Each FW transaction gets a fresh UUID, gets re-pushed, gets duplicated in YNAB.
- The user reconciles duplicates in YNAB manually once. (Same hard-cutoff pattern as the previous import_id migration.)

To avoid this, the user can delete `transactions.json` before the first run after this change — every transaction will then look "never synced," still creates duplicates of every previously-synced transaction, but transactions.json starts fresh and cleanly with the new semantics.

Either way, the migration produces duplicates once. User reconciles in YNAB. Subsequent runs are clean.

## Skip-all option (`q` key)

### Prompt UI

Add a new line to the per-transaction prompt:

```
  Or:
    s) Split into multiple categories
    c) Pick a category
    q) Quit categorizing — auto-flush remaining and finish
    f) Flush N pending to YNAB
```

(`q` always shows; `f` only when `unflushed_count > 0`.)

### Loop behaviour

In `_process_one_transaction`, add a new outcome `"quit"` to the return type. When the user types `q`:

```python
if raw == "q":
    return "quit"
```

In `sync_transactions`:

```python
if outcome == "quit":
    print(f"\nSkipping remaining {total - idx} transactions.")
    break  # exit the while loop; the normal end-of-loop auto-flush fires
```

The skipped transactions have no stored import_id (they never reached the rotation branch), so on the next run they appear as fresh "never synced" transactions and the user picks up where they left off.

## Fail loud

### What's swallowed today

| Location | Current behavior | Change |
|---|---|---|
| `_PendingQueue.flush` | `try/except Exception: print + return False` | Remove the try/except. Let exceptions propagate. |
| `sync_transactions` — FW fetch | `try/except: print + return` | Remove. Let it raise. |
| `sync_transactions` — YNAB transactions fetch | Same | Same. |
| `sync_transactions` — categories / category groups fetch | Same (silently uses empty list) | Same. Categories are essential for the prompt — silently empty was a bug. |
| `_create_new_category` — YNAB create_category_group / create_category failure | `try/except: print + return None` | Remove. Let it raise. |
| `_record_merchant_alias` — get_payees + create_payee | `try/except: print + fallback` | Remove. Let it raise. |

### What stays caught

- `KeyboardInterrupt` in `sync_transactions` — the user-friendly Ctrl+C flush-prompt is good UX.
- `_calculate_starting_balance` — currently catches and falls back to the raw FW balance. Acceptable: it's a helper that contributes to an account create, not a primary path. Leave as is for now (note as follow-up if user wants symmetry).

### Why this matters

The user's previous run silently exited after a flush failure — categorized N transactions, sent them to YNAB, got an exception, printed "Flush failed: ..." and ended. Standard exit code, no traceback, no indication something broke. Letting exceptions propagate gives the user (and the operating shell) a non-zero exit code, a full traceback, and a clear "this is broken" signal.

## File Changes Summary

| File | Change |
|---|---|
| `src/finab/transactions.py` | Add 2-line `ignore_transactions` skip in `merge_and_filter_transactions`. Rewrite dedup to use stable import_id + rotation. Simplify `_PendingQueue.flush` (no try/except, no transient correlators). Rename TransactionsStore methods. Add `"quit"` outcome to `_process_one_transaction`. Remove try/except blocks per fail-loud table. |
| `src/finab/main.py` | Filter `fw_transactions` list in `sync_merchants` before `_extract_distinct_merchants`. Remove try/except in `_record_merchant_alias` per fail-loud table. |
| `tests/test_transactions.py` | New tests: ignore-flag skip in merge_and_filter, stable-import_id dedup, UUID rotation on missing twin, `q` outcome, fail-loud (assertRaises on flush failure). Update existing TestPendingQueue tests that assumed flush swallowed exceptions. |
| `tests/test_sync_merchants.py` | New test for Phase 2 ignore filter. |

`ConfigStore` API is unchanged. `TransactionsStore` API renames as listed.

## Non-Goals

- **CLI prompt for setting the ignore flag.** Manual `config.json` edit only.
- **Heuristic detection of aggregator accounts.** Explicit user intent only.
- **Once-off cleanup of duplicate YNAB transactions already created.** User has already cleaned these up manually.
- **Removing ignored accounts from the store.** The account entry stays — only its transactions are filtered out.
- **Persistent partial-run state.** The `q` outcome is a clean break, not a "resume from transaction N" feature. Un-categorized transactions re-appear on next run as new.
- **Universal try/except removal.** `_calculate_starting_balance` keeps its fallback. Phase 1's account-create fallback prints and continues — out of scope for this change.
