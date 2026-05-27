# Phase 3 Follow-ups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement four Phase 3 improvements: per-account `ignore_transactions` flag, stable import_id with rotation, `q` to skip-all, and fail-loud error handling.

**Architecture:** Four sequenced, independent tasks. Tasks 1, 3 can be reordered freely. Task 2 reshapes `transactions.json` semantics and rewrites `merge_and_filter_transactions` + `_PendingQueue.flush`. Task 4 (fail-loud) lands last because it removes try/except blocks that the earlier tasks otherwise still rely on for behavioural compatibility.

**Tech Stack:** Python 3.14, pytest, `uv` package manager.

**Reference spec:** `docs/superpowers/specs/2026-05-27-phase3-followups-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/finab/transactions.py` | Modify | `merge_and_filter_transactions` gains `ignore_transactions` skip + new dedup logic. `_PendingQueue.flush` simplifies. `_process_one_transaction` gains `"quit"` outcome. `TransactionsStore` methods renamed. `sync_transactions` handles quit + drops swallowing try/except. |
| `src/finab/main.py` | Modify | `sync_merchants` filters FW transactions before extracting merchants. `_record_merchant_alias` stops swallowing exceptions. |
| `tests/test_transactions.py` | Modify | Multiple test classes updated; new tests for ignore flag, import_id rotation, `q` outcome, fail-loud (assertRaises). |
| `tests/test_sync_merchants.py` | Modify | New test for Phase 2 ignore filter. |

---

## Task 1: `ignore_transactions` flag

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `src/finab/main.py`
- Modify: `tests/test_transactions.py`
- Modify: `tests/test_sync_merchants.py`

- [ ] **Step 1: Append failing test for Phase 3 filter**

Add to `tests/test_transactions.py` inside the existing `TestMergeAndFilter` class:

```python
    def test_skips_account_marked_ignore_transactions(self):
        """Transactions whose FW account is flagged ignore_transactions
        are dropped — they never reach the YNAB sync."""
        accounts = list(self.store.accounts())
        self.store._data["accounts"][accounts[0]["id"]]["ignore_transactions"] = True
        self.store._save()
        self.store = ConfigStore(self.config_path)

        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(result, [])
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestMergeAndFilter::test_skips_account_marked_ignore_transactions -v`
Expected: FAIL — `AssertionError: [<MagicMock ...>] != []`. The transaction is currently returned because the flag isn't checked.

- [ ] **Step 3: Add the skip in `merge_and_filter_transactions`**

In `src/finab/transactions.py`, find the loop in `merge_and_filter_transactions`:

```python
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue
```

Insert the ignore-skip between the `not acc` check and the `ynab_account_id` lookup:

```python
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        if acc.get("ignore_transactions"):
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue
```

- [ ] **Step 4: Verify Phase 3 test passes**

Run: `uv run pytest tests/test_transactions.py::TestMergeAndFilter -v`
Expected: all PASS (5 tests).

- [ ] **Step 5: Add Phase 2 filter test**

Find the existing test file `tests/test_sync_merchants.py`. Inside the `TestSyncMerchants` class (the integration-style class that sets up `fw_client` / `ynab_client` mocks), add:

```python
    @patch("finab.main.input", create=True, return_value="Spar")
    def test_skips_merchants_from_ignored_accounts(self, _input):
        """Phase 2's merchant extraction filters out transactions whose
        FW account is flagged ignore_transactions."""
        # Add an account flagged as ignored.
        self.store.add_account(
            alias="Discovery Bank ZAR",
            fw_record={"id": "fw-zar"},
            ynab_record={"id": "yn-zar"},
        )
        accounts = list(self.store.accounts())
        # Mark the just-added account as ignored.
        zar = next(a for a in accounts if a["alias"] == "Discovery Bank ZAR")
        self.store._data["accounts"][zar["id"]]["ignore_transactions"] = True
        self.store._save()
        self.store = ConfigStore(self.config_path)

        # Two FW transactions, both pointing at the IGNORED account.
        ignored_txn = self._fw_txn("fw-only-on-ignored", "X")
        ignored_txn.account_id = "fw-zar"
        self.fw_client.get_transactions.return_value = [ignored_txn]
        self.ynab_client.get_payees.return_value = []

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        # The merchant was NOT extracted because its only transaction
        # belonged to an ignored account.
        self.assertIsNone(self.store.merchant_by_finwise_id("fw-only-on-ignored"))
        # And no YNAB payee was created.
        self.ynab_client.create_payee.assert_not_called()
```

Note: `_fw_txn` in this test file currently builds a MagicMock with `merchant_id` and `merchant_name` only; the test above mutates `account_id` after construction. If the `_fw_txn` helper doesn't already set `account_id`, set it on the returned mock.

- [ ] **Step 6: Run Phase 2 test, expect fail**

Run: `uv run pytest tests/test_sync_merchants.py::TestSyncMerchants::test_skips_merchants_from_ignored_accounts -v`
Expected: FAIL — the merchant IS extracted because Phase 2 doesn't filter yet.

- [ ] **Step 7: Add Phase 2 filter in `sync_merchants`**

In `src/finab/main.py`, find the `sync_merchants` function. Locate where it calls `fw_client.get_transactions()` and immediately passes the result to `_extract_distinct_merchants`. Insert a filter between them. The current code looks like:

```python
    fw_transactions = fw_client.get_transactions()
    ...
    fw_merchants = _extract_distinct_merchants(fw_transactions)
```

Change to:

```python
    fw_transactions = fw_client.get_transactions()

    def _account_is_ignored(fw_account_id):
        acc = store.account_by_finwise_id(fw_account_id)
        return bool(acc and acc.get("ignore_transactions"))

    fw_transactions = [
        t for t in fw_transactions
        if not _account_is_ignored(getattr(t, "account_id", None))
    ]
    ...
    fw_merchants = _extract_distinct_merchants(fw_transactions)
```

- [ ] **Step 8: Verify Phase 2 test passes**

Run: `uv run pytest tests/test_sync_merchants.py -v`
Expected: all PASS.

- [ ] **Step 9: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add src/finab/transactions.py src/finab/main.py tests/test_transactions.py tests/test_sync_merchants.py
git commit -m "feat: ignore_transactions flag on account entries

Phase 2 (merchant extraction) and Phase 3 (transaction sync) both
filter out transactions whose FinWise account carries
ignore_transactions: true. Fixes the Discovery-Bank-ZAR-as-aggregator
duplicate-transactions case.

Optional, defaults to false. No CLI; manual config.json edit.

Spec: docs/superpowers/specs/2026-05-27-phase3-followups-design.md"
```

---

## Task 2: Stable import_id with rotation

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Rename `TransactionsStore` methods**

In `src/finab/transactions.py`, find the `TransactionsStore` class and rename methods:

| Old | New |
|---|---|
| `synced_ynab_id(fw_uuid)` | `import_id_for(fw_uuid)` |
| `record_sync(fw_uuid, ynab_uuid)` | `record(fw_uuid, import_id)` |
| `record_syncs(mappings)` | (delete entirely) |
| `remove_sync(fw_uuid)` | `remove(fw_uuid)` |
| `prune_stale(live_ynab_ids: set)` | `prune_stale(live_import_ids: set)` (semantic rename; signature unchanged) |

Replace the existing class body with:

```python
class TransactionsStore:
    """Owns transactions.json: a map from FinWise transaction UUIDs to our
    durable YNAB import_id (a random uuid4 hex). The import_id is sent to
    YNAB on each push and is what we dedup against on subsequent fetches.

    When a previously-synced YNAB transaction is missing from the live
    YNAB fetch (user deleted it), the import_id is rotated: a new uuid is
    generated, replacing the stored one, and the FW transaction is pushed
    as new. This sidesteps YNAB's phantom-import_id behaviour (which would
    otherwise silently no-op a re-push using the deleted-but-remembered id).
    """

    def __init__(self, path: Path = TRANSACTIONS_FILE):
        self.path = Path(path)
        self._data = self._load()
        self._data.setdefault("synced_transactions", {})

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=4, default=str)
        os.replace(tmp, self.path)

    def import_id_for(self, fw_uuid: str) -> Optional[str]:
        """Return the durable YNAB import_id we previously assigned to
        this FinWise transaction, or None if it hasn't been synced."""
        return self._data["synced_transactions"].get(fw_uuid)

    def record(self, fw_uuid: str, import_id: str) -> None:
        """Persist a single fw -> import_id mapping."""
        self._data["synced_transactions"][fw_uuid] = import_id
        self._save()

    def remove(self, fw_uuid: str) -> None:
        """Drop a stale mapping."""
        if fw_uuid in self._data["synced_transactions"]:
            del self._data["synced_transactions"][fw_uuid]
            self._save()

    def prune_stale(self, live_import_ids: set) -> int:
        """Drop any mapping whose import_id is not in the live YNAB fetch.
        Returns the number of entries removed."""
        kept = {
            fw: iid
            for fw, iid in self._data["synced_transactions"].items()
            if iid in live_import_ids
        }
        removed = len(self._data["synced_transactions"]) - len(kept)
        if removed:
            self._data["synced_transactions"] = kept
            self._save()
        return removed
```

- [ ] **Step 2: Rewrite `merge_and_filter_transactions` dedup logic**

In `src/finab/transactions.py`, replace the existing `merge_and_filter_transactions` body. Current code:

```python
def merge_and_filter_transactions(
    fw_transactions,
    ynab_transactions,
    store: ConfigStore,
    tx_store: "TransactionsStore",
) -> list:
    """..."""
    ynab_by_id = {
        str(t.id): t
        for t in ynab_transactions
        if not getattr(t, "deleted", False)
    }

    tx_store.prune_stale(set(ynab_by_id.keys()))

    out = []
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        if acc.get("ignore_transactions"):
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue

        fw_uuid = fw_txn.import_id

        prev_ynab_id = tx_store.synced_ynab_id(fw_uuid) if fw_uuid else None
        if prev_ynab_id:
            ynab_match = ynab_by_id.get(prev_ynab_id)
            if ynab_match is None:
                pass
            elif getattr(ynab_match, "category_id", None):
                continue
            else:
                fw_txn.ynab_id = prev_ynab_id
                fw_txn.category_id = None

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out
```

Replace with:

```python
def merge_and_filter_transactions(
    fw_transactions,
    ynab_transactions,
    store: ConfigStore,
    tx_store: "TransactionsStore",
) -> list:
    """Map FinWise accounts to YNAB account ids via the store, dedup against
    our stable import_id stored in transactions.json, and skip transactions
    already categorized in YNAB. Returns the list of FinWise transactions
    needing processing.

    Each FW transaction passes through one of these paths:
      1. account unknown OR ignore_transactions=True -> drop
      2. previously synced + YNAB twin exists + categorized -> drop
         (preserve user's manual categorization in YNAB)
      3. previously synced + YNAB twin exists + uncategorized -> mark for
         update (set fw_txn.ynab_id; keep stable import_id)
      4. previously synced + YNAB twin missing -> rotate (generate fresh
         uuid, overwrite stored, push as new) — handles user-side deletes
      5. never synced -> new transaction (generate uuid, record, push)
    """
    # Index live YNAB transactions by their import_id (skip deleted).
    ynab_by_import_id = {
        str(t.import_id): t
        for t in ynab_transactions
        if not getattr(t, "deleted", False) and getattr(t, "import_id", None)
    }

    # Prune any stored import_ids no longer present in YNAB.
    tx_store.prune_stale(set(ynab_by_import_id.keys()))

    out = []
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
            # Already synced and YNAB still has it.
            ynab_match = ynab_by_import_id[our_id]
            if getattr(ynab_match, "category_id", None):
                # Already categorized — preserve user's manual work.
                continue
            fw_txn.ynab_id = str(ynab_match.id)
            fw_txn.import_id = our_id  # keep stable for update push
            fw_txn.category_id = None
        else:
            # Either never synced, or YNAB-twin missing (user deleted).
            # Rotate: fresh UUID, overwrite stored, push as new.
            new_id = uuid.uuid4().hex
            tx_store.record(fw_uuid, new_id)
            fw_txn.import_id = new_id

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out
```

- [ ] **Step 3: Simplify `_PendingQueue.flush`**

In `src/finab/transactions.py`, find `_PendingQueue.flush`. Current code:

```python
    def flush(self, ynab_client: YNABClient, budget_id: str, tx_store: Optional["TransactionsStore"] = None) -> bool:
        """..."""
        try:
            creates_snap = list(self.creates)
            updates_snap = list(self.updates)

            correlation_map: dict[str, str] = {}
            for txn in creates_snap:
                fw_uuid = getattr(txn, "import_id", None)
                if not fw_uuid:
                    continue
                correlator = uuid.uuid4().hex[:32]
                correlation_map[correlator] = fw_uuid
                txn.import_id = correlator

            create_response = None
            if creates_snap:
                create_response = ynab_client.create_transactions(budget_id, creates_snap)
            if updates_snap:
                ynab_client.update_transactions(budget_id, updates_snap)

            if create_response is not None and tx_store is not None:
                mappings = {}
                try:
                    returned = create_response.data.transactions or []
                except AttributeError:
                    returned = []
                for ynab_txn in returned:
                    yn_import = getattr(ynab_txn, "import_id", None)
                    fw_uuid = correlation_map.get(yn_import) if yn_import else None
                    if fw_uuid:
                        mappings[fw_uuid] = str(ynab_txn.id)
                if mappings:
                    tx_store.record_syncs(mappings)

            self.creates.clear()
            self.updates.clear()
            return True
        except Exception as e:
            print(f"Flush failed: {e}")
            return False
```

Replace with:

```python
    def flush(self, ynab_client: YNABClient, budget_id: str) -> bool:
        """Push all pending transactions in two batched calls. Returns True
        on success. Raises on failure (no swallowing — see fail-loud spec).

        txn.import_id is already the durable UUID set by
        merge_and_filter_transactions; we send it through as-is.
        """
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

The `tx_store` parameter is removed from the signature — no post-flush mapping recording is needed (the mapping was already written by `merge_and_filter`). Callers in `sync_transactions` must drop the `tx_store` argument.

- [ ] **Step 4: Update `sync_transactions` callers of `flush`**

In `src/finab/transactions.py`, find the three `queue.flush(...)` calls inside `sync_transactions`:

```python
            if outcome == "flush":
                queue.flush(ynab_client, budget_id, tx_store)
                continue
            ...
        if queue.count() > 0:
            queue.flush(ynab_client, budget_id, tx_store)
    except KeyboardInterrupt:
        if queue.count() > 0:
            if _confirm(f"\nFlush {queue.count()} pending transactions before exit? [Y/n]: "):
                queue.flush(ynab_client, budget_id, tx_store)
        raise
```

Drop the `tx_store` argument from each:

```python
            if outcome == "flush":
                queue.flush(ynab_client, budget_id)
                continue
            ...
        if queue.count() > 0:
            queue.flush(ynab_client, budget_id)
    except KeyboardInterrupt:
        if queue.count() > 0:
            if _confirm(f"\nFlush {queue.count()} pending transactions before exit? [Y/n]: "):
                queue.flush(ynab_client, budget_id)
        raise
```

- [ ] **Step 5: Update existing `TestPendingQueue` tests**

In `tests/test_transactions.py`, find the `TestPendingQueue` class. The tests call `q.flush(client, "bid")` (no third arg) so the signature change to remove `tx_store` already works. But the test for `test_flush_failure_keeps_queue` currently expects `flush` to return False on exception. With Task 4 it'll raise — but for THIS task, we're moving the try/except removal there. So for now, keep `test_flush_failure_keeps_queue` as-is (it still passes — see below).

Wait — actually Step 3 above already removed the try/except. So the test now fails because the exception is raised, not caught. Update the test:

Find:

```python
    def test_flush_failure_keeps_queue(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        client.create_transactions.side_effect = RuntimeError("network")
        ok = q.flush(client, "bid")
        self.assertFalse(ok)
        self.assertEqual(q.count(), 1)
```

Replace with:

```python
    def test_flush_failure_raises(self):
        """Failures propagate. The queue may be in any state on failure;
        the contract is just 'an exception reaches the orchestrator'."""
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        client.create_transactions.side_effect = RuntimeError("network")
        with self.assertRaises(RuntimeError):
            q.flush(client, "bid")
```

- [ ] **Step 6: Update `TestPendingQueueFlushRecordsMappings`**

This existing test class verifies the OLD correlator + record_syncs behaviour. With the new design, mappings are written by `merge_and_filter` (not by flush), so these tests are obsolete. Replace the class entirely:

Find the class `TestPendingQueueFlushRecordsMappings` (with `test_flush_records_fw_to_ynab_via_correlator`, `test_flush_does_not_record_when_no_tx_store`, `test_flush_works_with_real_transaction_model`). Replace with:

```python
class TestPendingQueueFlushPassesImportId(unittest.TestCase):
    """After the stable-import_id refactor, flush sends txn.import_id
    (already a durable UUID set by merge_and_filter_transactions) directly
    to YNAB — no transient correlators, no post-flush mapping writes."""

    def test_flush_sends_txn_import_id_as_is(self):
        from finab.transactions import _PendingQueue
        q = _PendingQueue()
        t1 = MagicMock(ynab_id=None, import_id="stable-uuid-1")
        t2 = MagicMock(ynab_id=None, import_id="stable-uuid-2")
        q.add(t1)
        q.add(t2)

        ynab_client = MagicMock()
        q.flush(ynab_client, "bid")

        # The import_id on each txn was NOT mutated by flush.
        self.assertEqual(t1.import_id, "stable-uuid-1")
        self.assertEqual(t2.import_id, "stable-uuid-2")
        ynab_client.create_transactions.assert_called_once()

    def test_flush_works_with_real_transaction_model(self):
        """Regression: pydantic v2's Transaction model rejects undeclared
        attribute assignment. The new flush is simpler — no side-attr
        stash — so this is just a smoke test that real Transaction
        instances flow through cleanly."""
        from datetime import date as d
        from finab.models import Transaction
        from finab.transactions import _PendingQueue

        q = _PendingQueue()
        txn = Transaction(
            account_id="yn-acc",
            date=d(2026, 5, 20),
            amount=-5000,
            import_id="stable-uuid",
        )
        q.add(txn)
        ynab_client = MagicMock()
        q.flush(ynab_client, "bid")
        self.assertEqual(txn.import_id, "stable-uuid")
```

- [ ] **Step 7: Update `TestTransactionsStore` for renamed methods**

Find `TestTransactionsStore` class. Update all method calls to use new names. Replace the class entirely:

```python
class TestTransactionsStore(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "transactions.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_starts_empty(self):
        s = TransactionsStore(self.path)
        self.assertIsNone(s.import_id_for("anything"))

    def test_record_and_retrieve(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "import-id-1")
        self.assertEqual(s.import_id_for("fw-1"), "import-id-1")
        # Persists across reload
        s2 = TransactionsStore(self.path)
        self.assertEqual(s2.import_id_for("fw-1"), "import-id-1")

    def test_remove(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "import-id-1")
        s.remove("fw-1")
        self.assertIsNone(s.import_id_for("fw-1"))

    def test_prune_stale_removes_orphans(self):
        s = TransactionsStore(self.path)
        s.record("fw-1", "iid-keep")
        s.record("fw-2", "iid-gone")
        removed = s.prune_stale({"iid-keep"})
        self.assertEqual(removed, 1)
        self.assertEqual(s.import_id_for("fw-1"), "iid-keep")
        self.assertIsNone(s.import_id_for("fw-2"))
```

- [ ] **Step 8: Update `TestMergeAndFilter` tests for new semantics**

Find `TestMergeAndFilter` class. The existing tests use `tx_store.record_sync` (renamed to `record`) and `synced_ynab_id` (renamed to `import_id_for`). The semantics also change: the stored value is now an import_id we match against `ynab_txn.import_id`, not against `ynab_txn.id`. Update each test:

Find `test_skips_already_categorized_match`:

```python
    def test_skips_already_categorized_match(self):
        self.tx_store.record_sync("fw-tx-1", "yn-tx-1")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", -1000, category_id="cat-X")]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(result, [])
```

Replace with:

```python
    def test_skips_already_categorized_match(self):
        """A FW txn whose stored import_id matches a YNAB txn that is
        already categorized is dropped (preserve manual YNAB edits)."""
        self.tx_store.record("fw-tx-1", "import-id-A")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn(
            "yn-tx-1", -1000, category_id="cat-X", import_id="import-id-A"
        )]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(result, [])
```

Find `test_links_uncategorized_ynab_match_for_update`:

```python
    def test_links_uncategorized_ynab_match_for_update(self):
        self.tx_store.record_sync("fw-tx-1", "yn-tx-1")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", -1000, category_id=None)]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "yn-tx-1")
```

Replace with:

```python
    def test_links_uncategorized_ynab_match_for_update(self):
        """A FW txn whose stored import_id matches an uncategorized YNAB
        txn is marked for update (ynab_id set, import_id kept stable)."""
        self.tx_store.record("fw-tx-1", "import-id-A")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn(
            "yn-tx-1", -1000, category_id=None, import_id="import-id-A"
        )]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "yn-tx-1")
        # Import_id stays stable for update push.
        self.assertEqual(result[0].import_id, "import-id-A")
```

Find `test_treats_as_new_when_ynab_twin_was_deleted`:

```python
    def test_treats_as_new_when_ynab_twin_was_deleted(self):
        """If a previously-synced YNAB transaction was deleted, the stale
        mapping is pruned and the FW txn is treated as new (re-importable)."""
        self.tx_store.record_sync("fw-tx-1", "yn-tx-old")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0].ynab_id)
        self.assertIsNone(self.tx_store.synced_ynab_id("fw-tx-1"))
```

Replace with:

```python
    def test_rotates_when_ynab_twin_was_deleted(self):
        """If the stored import_id is no longer present in the live YNAB
        fetch, the entry is replaced with a fresh uuid and the FW txn is
        treated as new (will re-push with the new id)."""
        self.tx_store.record("fw-tx-1", "import-id-OLD")
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        # YNAB no longer has any txn with import_id=import-id-OLD.
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        # No update link (we're treating as new).
        self.assertIsNone(result[0].ynab_id)
        # The stored import_id was rotated to a fresh value.
        rotated = self.tx_store.import_id_for("fw-tx-1")
        self.assertIsNotNone(rotated)
        self.assertNotEqual(rotated, "import-id-OLD")
        # The fw_txn's import_id reflects the new id (will be sent on push).
        self.assertEqual(result[0].import_id, rotated)
```

Add a new test for the never-synced fresh-uuid path:

```python
    def test_assigns_fresh_uuid_for_never_synced(self):
        """A FW txn with no stored mapping gets a fresh uuid recorded and
        set as its import_id."""
        fw_txns = [self._fw_txn("fw-tx-new", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store, self.tx_store)
        self.assertEqual(len(result), 1)
        assigned = self.tx_store.import_id_for("fw-tx-new")
        self.assertIsNotNone(assigned)
        self.assertEqual(result[0].import_id, assigned)
        self.assertIsNone(result[0].ynab_id)  # no update link
```

The `_ynab_txn` helper in this test class needs to accept an `import_id` parameter. Find:

```python
    def _ynab_txn(self, id, amount, category_id=None, deleted=False):
        t = MagicMock()
        t.id = id
        t.amount = amount
        t.category_id = category_id
        t.deleted = deleted
        t.transfer_account_id = None
        return t
```

Replace with:

```python
    def _ynab_txn(self, id, amount, category_id=None, deleted=False, import_id=None):
        t = MagicMock()
        t.id = id
        t.amount = amount
        t.category_id = category_id
        t.deleted = deleted
        t.transfer_account_id = None
        t.import_id = import_id
        return t
```

- [ ] **Step 9: Run tests, expect mostly pass**

Run: `uv run pytest -q`
Expected: mostly PASS. The previous `test_skips_account_marked_ignore_transactions` (from Task 1) should still PASS. Some tests may FAIL because of leftover references to the old method names or `record_syncs` — fix any that surface.

Run grep to catch leftovers:

```bash
grep -rn "record_sync\b\|record_syncs\|synced_ynab_id" src/ tests/
```

Expected: no matches in `src/`. Test files should only show the lines we explicitly updated in Steps 5-8.

- [ ] **Step 10: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): stable import_id with rotation on YNAB-twin-missing

transactions.json schema flips from {fw_uuid -> ynab_uuid} to
{fw_uuid -> our_import_id}. The import_id is a random uuid4 hex, sent
to YNAB as import_id, and used as the dedup key against YNAB's
import_id field on each fetch.

merge_and_filter_transactions:
- Indexes YNAB transactions by import_id (skipping deleted)
- For each FW txn, looks up stored import_id and matches against YNAB
- Categorized YNAB twin -> drop (preserve manual edits)
- Uncategorized YNAB twin -> mark for update, keep stable import_id
- Missing YNAB twin OR never synced -> rotate (fresh uuid, overwrite
  stored, treat as new push)

_PendingQueue.flush simplifies: transient correlator UUIDs gone,
post-flush record_syncs gone. txn.import_id is the durable id and gets
sent through as-is. tx_store parameter dropped from the signature.

TransactionsStore method renames:
- synced_ynab_id -> import_id_for
- record_sync   -> record
- record_syncs  -> (deleted)
- remove_sync   -> remove
- prune_stale: argument semantically renamed to live_import_ids

Migration: hard cutoff (per spec). Existing transactions.json entries
are interpreted as import_ids on first run; YNAB has no transactions
with those import_ids so every FW transaction looks new and gets a
fresh uuid. User reconciles one batch of duplicates in YNAB.

Spec: docs/superpowers/specs/2026-05-27-phase3-followups-design.md"
```

---

## Task 3: `q` to skip-all

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing test for the quit outcome**

Add to `tests/test_transactions.py` inside the existing `TestProcessOneTransaction` class:

```python
    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="q")
    def test_user_picks_quit(self, _input, _stdout):
        """Typing 'q' in the per-transaction prompt returns 'quit' so the
        orchestrator can break the loop and finish Phase 3."""
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={"-3000": {"parent_memo": "",
                                   "splits": [{"category_id": "c-petrol",
                                               "amount_milliunits": -3000,
                                               "memo": ""}]}},
        )
        self.store = ConfigStore(self.config_path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 5, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "quit")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestProcessOneTransaction::test_user_picks_quit -v`
Expected: FAIL — `q` is currently treated as an unrecognized input; the function loops re-prompting (which the mock can't service, leading to StopIteration / unexpected behaviour).

- [ ] **Step 3: Add `q` handling to `_process_one_transaction`**

In `src/finab/transactions.py`, find the input-handling loop near the bottom of `_process_one_transaction`:

```python
    while True:
        raw = input(_cyan("  > ")).strip().lower()
        if raw == "" and repeat_available:
            _apply_repeat(merchant, txn)
            return "categorized"
        if raw == "f" and unflushed_count:
            return "flush"
        if raw == "c":
            ...
```

Add a `q` branch immediately after `f`:

```python
        if raw == "f" and unflushed_count:
            return "flush"
        if raw == "q":
            return "quit"
        if raw == "c":
            ...
```

Also update the prompt-text-rendering block. Find:

```python
    print(f"  Or:")
    print(f"    {_dim('s)')} Split into multiple categories")
    print(f"    {_dim('c)')} Pick a category")
    if unflushed_count:
        print(f"    {_dim('f)')} Flush {unflushed_count} pending to YNAB")
    print()
```

Insert the `q` option line:

```python
    print(f"  Or:")
    print(f"    {_dim('s)')} Split into multiple categories")
    print(f"    {_dim('c)')} Pick a category")
    print(f"    {_dim('q)')} Quit categorizing — auto-flush remaining and finish")
    if unflushed_count:
        print(f"    {_dim('f)')} Flush {unflushed_count} pending to YNAB")
    print()
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_transactions.py::TestProcessOneTransaction::test_user_picks_quit -v`
Expected: PASS.

- [ ] **Step 5: Update `sync_transactions` to handle `"quit"`**

In `src/finab/transactions.py`, find the orchestrator loop body inside `sync_transactions`:

```python
            if outcome == "flush":
                queue.flush(ynab_client, budget_id)
                continue
            if outcome == "categorized":
                queue.add(txn)
                ...
            idx += 1
```

Insert the `quit` branch BEFORE the `flush` branch:

```python
            if outcome == "quit":
                print(f"\nSkipping remaining {total - idx} transactions.")
                break
            if outcome == "flush":
                queue.flush(ynab_client, budget_id)
                continue
            if outcome == "categorized":
                ...
```

The `break` exits the `while idx < total:` loop. Control passes to the normal end-of-loop auto-flush.

- [ ] **Step 6: Add integration test for skip-all in `sync_transactions`**

Append to `tests/test_sync_transactions.py` inside `TestSyncTransactionsIntegration`:

```python
    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="q")
    def test_quit_breaks_loop_and_auto_flushes(self, _input, _stdout):
        """Typing 'q' on the first transaction skips all remaining and
        triggers the end-of-loop auto-flush for anything already in
        the queue (which is empty in this case)."""
        # Two outflow transactions; the user 'q's on the first.
        t1 = self._fw_txn("fw-tx-1", "fw-acc", -5000, "fw-spar", memo="a")
        t2 = self._fw_txn("fw-tx-2", "fw-acc", -7000, "fw-spar", memo="b")
        self.fw_client.get_transactions.return_value = [t1, t2]
        self.ynab_client.get_transactions.return_value = []
        cat = self._category("c-groceries", "Groceries")
        self.ynab_client.get_categories.return_value = [cat]
        self.ynab_client.get_category_groups_with_categories.return_value = []
        m = self.store.merchant_by_finwise_id("fw-spar")
        self.store.set_merchant_memory(
            m["id"],
            categories_used={"c-groceries": 1},
            processings={"-9999": {"parent_memo": "",
                                   "splits": [{"category_id": "c-groceries",
                                               "amount_milliunits": -9999,
                                               "memo": ""}]}},
        )
        self.store = ConfigStore(self.config_path)

        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

        # No transactions were categorized -> nothing pushed.
        self.ynab_client.create_transactions.assert_not_called()
```

- [ ] **Step 7: Run full suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py tests/test_sync_transactions.py
git commit -m "feat(transactions): 'q' to skip-all in categorization prompt

A new 'q' option in _process_one_transaction returns 'quit'. The
orchestrator catches it, prints how many transactions were skipped,
and breaks out of the categorization loop. The normal end-of-loop
auto-flush still fires for anything in the queue. Skipped transactions
have no stored import_id (since merge_and_filter rotates/assigns on the
read side only after a successful processing... wait, actually they
DO have a stored import_id because merge_and_filter ran before the
prompt loop — but the YNAB push never happened, so YNAB has no twin,
and the next run's prune_stale + rotate handles them cleanly).

Spec: docs/superpowers/specs/2026-05-27-phase3-followups-design.md"
```

Wait — re-reading my own commit message I notice a subtle issue: `merge_and_filter` assigns and records the import_id BEFORE the prompt loop. If the user `q`'s out, transactions in the queue but not yet flushed have a recorded import_id pointing at... nothing in YNAB. On next run, `prune_stale` will remove the stored entry (the import_id isn't in YNAB's response), and `merge_and_filter`'s rotate branch will assign a fresh one. So it self-heals. Good — the commit message is accurate but verbose. Trim to:

```bash
git commit -m "feat(transactions): 'q' to skip-all in categorization prompt

A new 'q' option in _process_one_transaction returns 'quit'. The
orchestrator catches it, prints how many transactions were skipped,
breaks out of the categorization loop, and the normal end-of-loop
auto-flush still fires.

Skipped transactions self-heal on next run: their stored import_id
isn't in YNAB (they were never pushed), prune_stale removes it,
merge_and_filter assigns a fresh one and treats them as new.

Spec: docs/superpowers/specs/2026-05-27-phase3-followups-design.md"
```

---

## Task 4: Fail loud

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `src/finab/main.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Remove try/except in `sync_transactions` fetch blocks**

In `src/finab/transactions.py`, find these blocks at the top of `sync_transactions`:

```python
    try:
        fw_txns = fw_client.get_transactions()
    except Exception as e:
        print(f"Failed to fetch FinWise transactions: {e}")
        return

    try:
        ynab_txns = ynab_client.get_transactions(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB transactions: {e}")
        return

    try:
        ynab_categories = ynab_client.get_categories(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB categories: {e}")
        ynab_categories = []

    try:
        category_groups = ynab_client.get_category_groups_with_categories(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB category groups: {e}")
        category_groups = []
```

Replace with:

```python
    fw_txns = fw_client.get_transactions()
    ynab_txns = ynab_client.get_transactions(budget_id)
    ynab_categories = ynab_client.get_categories(budget_id)
    category_groups = ynab_client.get_category_groups_with_categories(budget_id)
```

- [ ] **Step 2: Remove try/except in `_create_new_category`**

In `src/finab/transactions.py`, find:

```python
    chosen_group = None
    while chosen_group is None:
        raw = input(_cyan("  Pick: ")).strip().lower()
        if not raw or raw == "b":
            return None
        if raw == "n":
            grp_name = input(_cyan("  New group name (Enter to cancel): ")).strip()
            if not grp_name:
                return None
            try:
                new_grp = ynab_client.create_category_group(budget_id, grp_name)
            except Exception as e:
                print(f"  Failed to create category group: {e}")
                return None
            if not hasattr(new_grp, "categories") or new_grp.categories is None:
                new_grp.categories = []
            category_groups.append(new_grp)
            chosen_group = new_grp
        ...

    try:
        new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)
    except Exception as e:
        print(f"  Failed to create category: {e}")
        return None
```

Remove the two `try/except` blocks. The function becomes:

```python
    chosen_group = None
    while chosen_group is None:
        raw = input(_cyan("  Pick: ")).strip().lower()
        if not raw or raw == "b":
            return None
        if raw == "n":
            grp_name = input(_cyan("  New group name (Enter to cancel): ")).strip()
            if not grp_name:
                return None
            new_grp = ynab_client.create_category_group(budget_id, grp_name)
            if not hasattr(new_grp, "categories") or new_grp.categories is None:
                new_grp.categories = []
            category_groups.append(new_grp)
            chosen_group = new_grp
        elif raw.isdigit():
            ...

    new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)
    if chosen_group.categories is None:
        chosen_group.categories = []
    chosen_group.categories.append(new_cat)
    return str(new_cat.id)
```

- [ ] **Step 3: Remove try/except in `_record_merchant_alias`**

In `src/finab/main.py`, find `_record_merchant_alias`:

```python
    try:
        ynab_payees = ynab_client.get_payees(budget_id)
    except Exception:
        ynab_payees = []

    ...

    try:
        new_payee = ynab_client.create_payee(budget_id, alias)
        store.add_merchant(
            alias=alias,
            fw_record=fw_record,
            ynab_record=to_dict(new_payee),
        )
    except Exception as e:
        print(f"Failed to create YNAB payee '{alias}': {e}")
        # Fall back to an empty ynab record so the FW id is at least
        # captured; user can resolve via a Phase 2 re-run.
        store.add_merchant(alias=alias, fw_record=fw_record, ynab_record={})
```

Remove both try/except blocks. Becomes:

```python
    ynab_payees = ynab_client.get_payees(budget_id)

    ...

    new_payee = ynab_client.create_payee(budget_id, alias)
    store.add_merchant(
        alias=alias,
        fw_record=fw_record,
        ynab_record=to_dict(new_payee),
    )
```

- [ ] **Step 4: Add a fail-loud regression test**

Append to `tests/test_transactions.py`:

```python
class TestFailLoudFlush(unittest.TestCase):
    """A YNAB API exception during flush must propagate so the user sees
    the traceback and a non-zero exit. Silently swallowing was the
    behaviour that caused a categorize-everything-then-nothing-in-YNAB
    bug previously."""

    def test_flush_exception_propagates(self):
        from finab.transactions import _PendingQueue
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None, import_id="x"))
        ynab_client = MagicMock()
        ynab_client.create_transactions.side_effect = RuntimeError("YNAB 500")
        with self.assertRaises(RuntimeError) as cm:
            q.flush(ynab_client, "bid")
        self.assertIn("YNAB 500", str(cm.exception))


class TestFailLoudSyncTransactionsFetch(unittest.TestCase):
    """A fetch failure during sync_transactions must propagate."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store = ConfigStore(self.config_path)
        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_fw_get_transactions_failure_propagates(self):
        from finab.transactions import sync_transactions
        self.fw_client.get_transactions.side_effect = RuntimeError("FW down")
        with self.assertRaises(RuntimeError):
            sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

    def test_ynab_get_categories_failure_propagates(self):
        from finab.transactions import sync_transactions
        self.fw_client.get_transactions.return_value = []
        self.ynab_client.get_transactions.return_value = []
        self.ynab_client.get_categories.side_effect = RuntimeError("YNAB down")
        with self.assertRaises(RuntimeError):
            sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)
```

- [ ] **Step 5: Run, expect pass**

Run: `uv run pytest tests/test_transactions.py::TestFailLoudFlush tests/test_transactions.py::TestFailLoudSyncTransactionsFetch -v`
Expected: all PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS. If `_PendingQueue` flush tests in earlier classes had any swallowing assumptions left over from Task 2, fix them now.

- [ ] **Step 7: Commit**

```bash
git add src/finab/transactions.py src/finab/main.py tests/test_transactions.py
git commit -m "fix: fail loud on flush, fetch, and create exceptions

Previously _PendingQueue.flush caught every Exception, printed once,
returned False; sync_transactions caught fetch failures and degraded
silently (categories/groups silently empty); _create_new_category and
_record_merchant_alias caught and returned None / fallback. A real
production bug: flush failed silently after categorizing 50+
transactions and the run exited 0 with nothing in YNAB.

Removed try/except blocks in:
- _PendingQueue.flush
- sync_transactions fetch steps (fw_client.get_transactions,
  ynab_client.get_transactions, get_categories,
  get_category_groups_with_categories)
- _create_new_category (create_category_group, create_category)
- _record_merchant_alias (get_payees, create_payee)

Kept: KeyboardInterrupt handler in sync_transactions for the
user-friendly Ctrl+C flush-prompt. Phase 1 / _calculate_starting_balance
fallback left as-is (out of scope; flagged in spec non-goals).

Spec: docs/superpowers/specs/2026-05-27-phase3-followups-design.md"
```

---

## Self-Review Notes

**Spec coverage:**
- `ignore_transactions` flag (Phase 2 + Phase 3) → Task 1 (steps 1-10)
- Stable import_id schema + rotation → Task 2 (steps 1-10)
- `q` skip-all → Task 3 (steps 1-8)
- Fail-loud → Task 4 (steps 1-7)
- Migration (hard cutoff with one batch of duplicates) → documented in Task 2 Step 10 commit message

**Type / signature consistency:**
- `TransactionsStore.import_id_for`, `record`, `remove`, `prune_stale` used consistently from Task 2 onwards
- `_PendingQueue.flush(ynab_client, budget_id)` (no `tx_store` arg) used consistently from Task 2 Step 3 onwards
- `_process_one_transaction` outcome set `{"categorized", "flush", "quit"}` from Task 3 onwards
- All `merge_and_filter_transactions(fw, yn, store, tx_store)` callsites are 4-arg

**Placeholder scan:** no TBDs, no "similar to", no vague "handle errors" — all code shown verbatim.
