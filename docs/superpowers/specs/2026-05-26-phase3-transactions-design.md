# Phase 3: Transaction Sync — Design

**Status:** Approved (design phase)
**Date:** 2026-05-26
**Scope:** Brand-new `sync_transactions` built against the `ConfigStore` produced by Phases 1 & 2. Includes interactive categorization with per-merchant memory, splits, new-category creation, memos, and manual batch flushing.
**Out of scope:** Renaming/deleting merchants or accounts (covered by future "store edit" workflows). Regex rule sets (`payee_rules`, `categories`) are removed; they were a workaround for missing merchant-side resolution that Phases 1 & 2 now own.

## Motivation

After Phases 1 and 2, every FinWise account and merchant has a first-class store entry. The legacy transaction pipeline (`process_payee_aliases`, `process_categories`, `merge_and_filter_transactions`, etc. — ~700 lines) conflated four concerns: merchant resolution, payee aliasing, transfer detection, and categorization. Phases 1 + 2 now own the first three, leaving Phase 3 with one real job: **categorization + sync**.

Design goals for Phase 3:

1. **Per-merchant category memory.** Most merchants have a few recurring categories ("Spar is usually Groceries, sometimes Snacks"); the picker should surface them by frequency.
2. **Easy splits.** Multi-category transactions are common (a fuel-station receipt that includes snacks).
3. **Repeat by Enter when exact.** When the current transaction's amount matches the last processed amount for that merchant, Enter replays the previous decision verbatim.
4. **User-paced batching.** Decisions queue locally; a `f` command flushes the batch to YNAB on demand. Auto-flush at the end of the run.
5. **Auto-paths for the obvious cases.** Inflows (positive amounts), transfers (merchants linked to own-account transfer payees), and no-merchant transactions don't prompt.
6. **Resumable.** Ctrl+C prompts to flush pending; not-yet-flushed transactions re-appear on the next run (via `import_id` dedup) and can be re-categorized.

## Schema Additions

Merchants gain two new fields. Everything else in the store schema stays the same:

```json
"merchants": {
  "<internal_uuid>": {
    "id": "<internal_uuid>",
    "alias": "Spar",
    "finwise": { ... },
    "ynab":    { ... },

    "categories_used": {
      "<ynab_category_id_groceries>": 47,
      "<ynab_category_id_snacks>": 3
    },

    "last_processing": {
      "amount_milliunits": -160000,
      "parent_memo": "superspar rosmead western cape za",
      "splits": [
        { "category_id": "<groceries_id>", "amount_milliunits": -120000, "memo": "" },
        { "category_id": "<snacks_id>",    "amount_milliunits":  -40000, "memo": "" }
      ]
    }
  }
}
```

### Schema choices

- **`categories_used` is `{category_id: usage_count}`.** The picker sorts by count descending. Frequency-ranked memory.
- **`last_processing.splits` is uniform.** A single-category processing is represented as a one-element `splits` list, so the data shape doesn't bifurcate.
- **`amount_milliunits` is the transaction total.** Enter-to-repeat only fires when the current transaction's amount equals this value exactly.
- **Memos are stored** inside `last_processing` for completeness but are **not** replayed on Enter — Enter uses fresh defaults (FinWise description for the parent, empty for sub-transaction memos).

## Pipeline

New module: `src/finab/transactions.py`. Single orchestrator function plus categorization helpers in the same file.

```python
def sync_transactions(fw_client, ynab_client, budget_id, store):
    print("\n--- Transaction Sync ---")

    # 1. Fetch — ALL transactions, no date filter
    fw_txns   = fw_client.get_transactions()
    ynab_txns = ynab_client.get_transactions(budget_id)
    ynab_categories = ynab_client.get_categories(budget_id)
    category_groups = ynab_client.get_category_groups_with_categories(budget_id)

    # 2. Resolve account / dedup
    candidates = merge_and_filter_transactions(fw_txns, ynab_txns, store)

    # 3. Sort by (merchant_alias, date_asc) so all of one merchant's
    #    transactions process back-to-back — keeps the Enter-repeat
    #    default warm.
    candidates.sort(key=_sort_key(store))

    # 4. Process with pending queue + manual flush
    queue = _PendingQueue()
    total = len(candidates)
    try:
        idx = 0
        while idx < total:
            txn = candidates[idx]
            outcome = _process_one_transaction(
                txn, idx + 1, total, queue.count(),
                store, ynab_client, budget_id,
                ynab_categories, category_groups,
            )
            if outcome == "flush":
                queue.flush(ynab_client, budget_id)
                continue              # re-prompt the same transaction
            if outcome == "categorized":
                queue.add(txn)
                _update_merchant_memory(store, txn)
            idx += 1
    except KeyboardInterrupt:
        if queue.count() > 0 and _confirm(f"Flush {queue.count()} pending transactions before exit? [Y/n]"):
            queue.flush(ynab_client, budget_id)
        raise
    finally:
        if queue.count() > 0:
            queue.flush(ynab_client, budget_id)
```

### Pipeline choices

- **Fetch all transactions every run.** No `since_date` filter on either side. Dedup via hashed `import_id` is load-bearing.
- **`merge_and_filter_transactions` is reused.** Account mapping now uses `store.account_by_finwise_id`; the legacy fuzzy-match migration fallback is removed (your data has migrated).
- **Sort by `(merchant_alias, date_asc)`.** Within a merchant, oldest first. This keeps the Enter-repeat default warm and matches how humans batch-categorize.
- **Auto-flush in `finally`.** A normal end-of-run always flushes. Ctrl+C asks for confirmation before flushing, then re-raises.
- **Mid-loop `flush` re-prompts the current transaction.** `f` mid-prompt doesn't abandon the transaction — the loop re-renders it.

## Auto-paths (No Prompt)

These cases bypass the interactive flow entirely. Each results in a transaction added to `queue` (or skipped, if already in YNAB).

1. **Already categorized in YNAB.** `merge_and_filter_transactions` skips these — preserves user's manual YNAB edits.
2. **Positive amount → Inflow: Ready to Assign.** Look up the inflow category id from YNAB (candidates: `Inflow: Ready to Assign`, `Ready to Assign`, `Inflow: To be Budgeted`, `To be Budgeted`). Set `category_id`, queue.
3. **Merchant is a transfer.** If `store.merchant_by_finwise_id(txn.merchant_id)` returns an entry whose `ynab.transfer_account_id` is non-null, the merchant represents one of the user's own accounts. Set `payee_id = ynab.id`, clear `payee_name` and `category_id`, queue.
4. **No merchant_id on the transaction.** Push uncategorized. The user assigns a category later in YNAB's UI.

## Interactive Categorization Flow

This runs for transactions that survive all auto-paths.

### Prompt UI

```
━━━ Transaction 7 of 23  (6 unflushed) ━━━━━━━━━━━━━━━━━━━━━━━
  Merchant:  Spar
  Date:      2026-05-20
  Amount:    -162.55
  Memo:      superspar rosmead western cape za

  [Enter] to repeat last: Groceries -162.55
  Or:
    s) Split into multiple categories
    c) Pick a category
    f) Flush 6 pending to YNAB
```

- `[Enter] to repeat last:` line only renders when `merchant.last_processing.amount_milliunits == txn.amount` exactly.
- `f) Flush N pending to YNAB` line only renders when `queue.count() > 0`.

### `c` — Pick a single category

```
  Categories for 'Spar':
   1. Groceries (used 47×)
   2. Snacks    (used 3×)

   o) Other category
   n) New category
   b) Back

  Pick: 1
  Memo (Enter to keep 'superspar rosmead western cape za'):
```

- Numbered list sorted by `categories_used` count descending.
- `o`(ther): full-list picker over every active YNAB category, grouped by category group. Numbered + filterable (same `?` filter pattern as the merchant picker).
- `n`(ew): prompts for category name, then for a target group (with `n` again to create a fresh group), then calls `create_category`. Successful creation also adds the new category to the in-memory `ynab_categories` list and to `merchant.categories_used` with count 1.
- `b`(ack): cancels, returns to the top-level prompt.

After picking, prompt for a memo with the FinWise description as default. Enter to keep default, or type new text.

### `s` — Split

```
  How many splits? [2]: 3
  Split 1 of 3 — amount [54.18]: 60
    (category picker, same as above)
    Memo (Enter for none):
  Split 2 of 3 — amount [51.275]: 80
    (category picker)
    Memo (Enter for none):
  Split 3 of 3 — amount [22.55] (remaining):
    (category picker)
    Memo (Enter for none):

  Parent memo (Enter to keep 'superspar rosmead western cape za'):
```

- Split count is asked upfront.
- Default amount per split is `remaining / splits_left`. Last split's default is the exact remainder so the total always reconciles.
- Per-split memo defaults to empty (Enter = no memo on that sub-transaction).
- Parent memo (asked once after all splits) defaults to FinWise description.

### `[Enter]` — Repeat last

Applies `merchant.last_processing` verbatim: same categories, same amounts, same split structure. Memos use defaults (parent: FinWise description, splits: empty).

## New-Category Creation Flow

When the user picks `n` during category selection:

```
  New category name: Pet Supplies
  Target group:
    1. Monthly Bills
    2. Just for Fun
    3. Quality of Life Goals
    ...
    n) New group
    b) Back

  Pick: n
  New group name: Pets
  → Created group 'Pets' (id: <new-uuid>)
  → Created category 'Pet Supplies' in 'Pets' (id: <new-uuid>)
```

- If the user picks `n` for "New group", we call `create_category_group` first, then `create_category` with the new group's id.
- After creation, the new category is added to the in-memory `category_groups` and `ynab_categories` so subsequent transactions in the same run can pick it without re-fetching from YNAB.

## Pending Queue and Flush

```python
class _PendingQueue:
    def __init__(self):
        self.creates: list[Transaction] = []
        self.updates: list[Transaction] = []

    def count(self) -> int:
        return len(self.creates) + len(self.updates)

    def add(self, txn: Transaction) -> None:
        (self.updates if txn.ynab_id else self.creates).append(txn)

    def flush(self, ynab_client, budget_id) -> bool:
        try:
            if self.creates:
                ynab_client.create_transactions(budget_id, self.creates)
            if self.updates:
                ynab_client.update_transactions(budget_id, self.updates)
            self.creates.clear()
            self.updates.clear()
            return True
        except Exception as e:
            print(f"Flush failed: {e}")
            return False
```

### Queue choices

- **Per-batch atomicity.** Either both create and update calls succeed (queue clears) or neither (queue retains state for next flush).
- **Merchant memory updates eagerly** — when a transaction is categorized, not when it's flushed. So within one Phase 3 run, picking Groceries for Spar makes it the top-ranked option for the next Spar prompt immediately.
- **Eager memory + lazy push** has a benign inconsistency: if the user Ctrl+Cs without flushing, the store's `last_processing` reflects un-pushed decisions. Next run will re-prompt those transactions (dedup misses since not in YNAB), and Enter-repeat will offer the same answer the user originally gave — exactly what they want.

## Merchant Memory Updates

When a transaction is categorized (before flush):

```python
def _update_merchant_memory(store, txn):
    merchant = store.merchant_by_finwise_id(txn.original_finwise_merchant_id)
    if not merchant:
        return
    # Build the new last_processing entry
    if txn.subtransactions:
        splits = [
            {"category_id": s["category_id"],
             "amount_milliunits": s["amount"],
             "memo": s.get("memo", "")}
            for s in txn.subtransactions
        ]
    else:
        splits = [
            {"category_id": txn.category_id,
             "amount_milliunits": txn.amount,
             "memo": txn.memo or ""}
        ]
    # Increment counts
    new_used = dict(merchant.get("categories_used", {}))
    for s in splits:
        cid = s["category_id"]
        new_used[cid] = new_used.get(cid, 0) + 1
    # Write back via store API
    store.set_merchant_memory(
        merchant["id"],
        categories_used=new_used,
        last_processing={
            "amount_milliunits": txn.amount,
            "parent_memo": txn.memo or "",
            "splits": splits,
        },
    )
```

New `ConfigStore.set_merchant_memory(merchant_id, categories_used, last_processing)` method (writes both fields atomically and persists via `_save`).

## YNABClient Additions

Two new methods in `src/finab/ynab_client.py`:

```python
def create_category(self, budget_id: str, name: str, category_group_id: str) -> Any:
    """Create a new category in an existing group."""
    categories_api = CategoriesApi(self.api_client)
    payload = SaveCategory(name=name, category_group_id=category_group_id)
    response = categories_api.create_category(
        budget_id, PostCategoryWrapper(category=payload)
    )
    return response.data.category

def create_category_group(self, budget_id: str, name: str) -> Any:
    """Create a new category group."""
    categories_api = CategoriesApi(self.api_client)
    payload = SaveCategoryGroup(name=name)
    response = categories_api.create_category_group(
        budget_id, PostCategoryGroupWrapper(category_group=payload)
    )
    return response.data.category_group

def get_category_groups_with_categories(self, budget_id: str):
    """Returns category groups with nested categories — preserves YNAB's
    hierarchy for the 'pick from full list' UI."""
    response = CategoriesApi(self.api_client).get_categories(budget_id)
    return response.data.category_groups
```

SDK model names verified against `ynab==4.1.0`: `ynab.models.save_category.SaveCategory(name, category_group_id, ...)`, `ynab.models.save_category_group.SaveCategoryGroup(name)`, `ynab.models.post_category_wrapper.PostCategoryWrapper(category=...)`, `ynab.models.post_category_group_wrapper.PostCategoryGroupWrapper(category_group=...)`.

## Cleanup — Code to Delete

From `src/finab/main.py`:

| Symbol | Approx lines | Reason |
|---|---|---|
| `process_payee_aliases` | 210 | Phase 2 owns payee resolution; in-loop merchant prompts unreachable. |
| `process_categories` | 360 | Replaced by `_categorize_interactive` in `transactions.py`. |
| `collect_split_subtransactions` | 70 | Replaced by new split flow inline. |
| `_build_cache`, `_apply_cache` | 50 | No cache file in new design. |
| `map_accounts` | 45 | Trivial inline `store.account_by_finwise_id` call. |
| `fetch_transactions` | 30 | Inlined into `sync_transactions`. |
| `sync_changes_to_ynab` | 65 | Replaced by `_PendingQueue.flush`. |
| `sync_transactions` (old) | 50 | Replaced by new module's version. |
| `normalize_payee_for_matching` | 20 | Unused after fuzzy-match removal. |

From `src/finab/config.py`:

- `load_payee_rules`, `save_payee_rules` (regex rules — not read/written anymore)
- `load_category_rules`, `save_category_rules` (same)
- `load_cache`, `save_cache`, `clear_cache` (no cache file)
- `CACHE_FILE` constant

From `src/finab/main.py` imports: drop references to the deleted symbols.

After deletion, `main.py` shrinks from ~1,714 lines to roughly 900. New `transactions.py` is ~400 lines including helpers.

`config.json`: legacy `payee_rules`, `categories`, `account_aliases`, `merchant_aliases` keys are dead data. The new code never reads them. Manual cleanup is optional.

## Non-Goals

- **Rewinding decisions.** Once a transaction is flushed, undoing happens in YNAB's UI. No "edit last" command.
- **Multi-budget support.** Single budget, as today.
- **Schedule integration.** Recurring transactions aren't modeled.
- **Category renaming / deletion via the CLI.** Use YNAB's UI.
- **Custom inflow categories.** Positive amounts always route to "Inflow: Ready to Assign" (or its variants). No override mechanism.
- **Offsetting refunds.** A positive amount from "Engen" doesn't get linked to a previous Engen expense; it becomes an inflow. Future work could add refund-detection.

## Open Implementation Questions

These are intentionally not resolved at design time; flag during implementation:

- **Pagination on YNAB API.** Fetching full history without pagination should be fine for personal volumes, but if any account has >10k transactions, behavior of the SDK's `get_transactions` should be verified.
- **`merge_and_filter_transactions` slim-down.** The legacy fuzzy-match path can be removed; mapping should be reworked to use `store.account_by_finwise_id` directly rather than the compat shim.
- **Category lookup performance.** With many YNAB categories, the in-memory list is fine. If categories ever number in the thousands, build a dict for O(1) lookup at startup.
