# Amount-Keyed Processings — Design

**Status:** Approved (design phase)
**Date:** 2026-05-27
**Scope:** Extend per-merchant categorization memory to track every distinct amount seen, not just the most recent. The interactive flow stays prompt-driven: Enter-repeat now fires when the current transaction's amount matches *any* previously-categorized amount for that merchant, not only the last one.
**Out of scope:** Fuzzy/tolerance matching (variable-fee merchants like petrol still need a fresh category pick per amount). Auto-apply-without-prompt — Enter is still required.

## Motivation

After Phase 3 shipped, merchants gained `categories_used` (frequency map) and `last_processing` (single most-recent split structure for exact-amount Enter-repeat). In practice, many merchants charge a small set of recurring amounts:

- Lifestyle on Kloof: always -R20 (parking flat fee)
- Spar at the corner: -R49.99 (snacks run) or -R150-300 (groceries)
- Apple subscription: always -R29.99
- A YogaLoft class pass: always -R20

`last_processing` keeps memory for only the most-recent amount. If you process -R20 → "Parking", then -R200 → "Groceries", the next -R20 transaction has *lost* the "Parking" memory (overwritten by -R200's entry). You re-enter the same category.

Tracking memory keyed by amount preserves all previously-seen amount-to-category mappings, so any matching amount triggers Enter-repeat regardless of which one was most recent.

## Schema

### Before

```json
"merchants": {
  "<internal_uuid>": {
    ...,
    "categories_used": { "<cat_id>": 47 },
    "last_processing": {
      "amount_milliunits": -160000,
      "parent_memo": "...",
      "splits": [ { "category_id": "<cat_id>", "amount_milliunits": -160000, "memo": "" } ]
    }
  }
}
```

### After

```json
"merchants": {
  "<internal_uuid>": {
    ...,
    "categories_used": { "<cat_id>": 47 },
    "processings": {
      "-160000": {
        "parent_memo": "...",
        "splits": [ { "category_id": "<cat_id>", "amount_milliunits": -160000, "memo": "" } ]
      },
      "-49990": {
        "parent_memo": "...",
        "splits": [ { "category_id": "<cat_snacks>", "amount_milliunits": -49990, "memo": "" } ]
      }
    }
  }
}
```

### Schema notes

- **Key shape:** `str(amount_milliunits)`. The signed-integer milliunits get stringified for JSON (e.g., `"-160000"` for an outflow of R160). Lookups use `str(txn.amount)` for symmetry.
- **Value shape:** identical to today's `last_processing`, minus the now-redundant `amount_milliunits` (which is the key). `parent_memo` + `splits` are preserved as-is.
- **`categories_used` is unchanged.** The frequency-counted map still drives the picker's ranking and is updated independently on each categorization.
- **`last_processing` is removed.** It's fully subsumed by `processings`.

## Migration

A one-shot, idempotent migration runs inside `ConfigStore._rebuild_indexes`:

```python
for m in self._data.get("merchants", {}).values():
    if "last_processing" in m and "processings" not in m:
        lp = m.pop("last_processing")
        key = str(lp.get("amount_milliunits"))
        m["processings"] = {
            key: {
                "parent_memo": lp.get("parent_memo", ""),
                "splits": lp.get("splits", []),
            }
        }
```

After the first run with this code:
- Every existing merchant with `last_processing` becomes `processings: {<that_amount>: <that_entry>}`.
- Subsequent runs see `processings` present and skip the migration.
- New merchants added by Phase 2 from this point forward write `processings` directly via `set_merchant_memory`.

No data is lost. No save is needed during read — `_rebuild_indexes` mutates `self._data` in memory; the migration persists when `_save` next runs (during any normal write operation: `add_account`, `add_merchant`, `set_merchant_memory`, etc.).

To be safe — so a read-only run doesn't leave the migrated shape un-persisted in case of crash mid-session — `_rebuild_indexes` calls `_save()` at the end if any migration occurred. Idempotent: subsequent runs find nothing to migrate and skip the save.

## Behaviour Changes

### Enter-repeat detection (`_can_repeat`)

```python
def _can_repeat(merchant: dict, txn) -> bool:
    if not merchant:
        return False
    processings = merchant.get("processings") or {}
    amt = getattr(txn, "amount", None)
    if amt is None:
        return False
    return str(amt) in processings
```

Before: `lp.get("amount_milliunits") == txn.amount`. After: `str(txn.amount) in merchant["processings"]`.

### Enter-repeat replay (`_apply_repeat`)

```python
def _apply_repeat(merchant: dict, txn) -> None:
    entry = merchant["processings"][str(txn.amount)]
    splits = entry.get("splits", []) or []
    if len(splits) == 1:
        txn.category_id = splits[0]["category_id"]
        txn.subtransactions = []
    else:
        txn.category_id = None
        txn.subtransactions = [
            {
                "category_id": s["category_id"],
                "amount": s["amount_milliunits"],
                "memo": "",
            }
            for s in splits
        ]
```

Looks up the matching entry by amount, replays the split structure. Memos use fresh defaults — same as today.

### Memory update (`_update_merchant_memory`)

```python
new_entry = {
    "parent_memo": getattr(txn, "memo", "") or "",
    "splits": splits,
}
processings = dict(merchant.get("processings", {}) or {})
processings[str(txn.amount)] = new_entry  # most recent decision wins per amount

store.set_merchant_memory(
    merchant["id"],
    categories_used=counts,
    processings=processings,
)
```

Inserts (or replaces) the entry for the current amount. If you re-categorize a -R20 Lifestyle on Kloof transaction from "Parking" to "Coffee", the next -R20 transaction's Enter-repeat now offers "Coffee" — the most recent decision wins, scoped to that amount.

### Store API (`ConfigStore.set_merchant_memory`)

Signature changes:

```python
def set_merchant_memory(
    self,
    merchant_id: str,
    categories_used: dict,
    processings: dict,   # was: last_processing: dict
) -> None:
    m = self._data["merchants"][merchant_id]
    m["categories_used"] = dict(categories_used)
    m["processings"] = dict(processings)
    self._rebuild_indexes()
    self._save()
```

Single internal caller (`_update_merchant_memory` in `transactions.py`) updates atomically.

### Prompt UI (`_process_one_transaction`)

The "[Enter] to repeat last:" preview line reads from `merchant["processings"][str(txn.amount)]` instead of `merchant["last_processing"]`. Otherwise unchanged.

## File Changes Summary

| File | Change |
|---|---|
| `src/finab/store.py` | `_rebuild_indexes` runs the one-shot `last_processing → processings` migration (saves if any merchant migrated). `set_merchant_memory` signature changes from `last_processing` to `processings`. |
| `src/finab/transactions.py` | `_can_repeat`, `_apply_repeat`, `_update_merchant_memory`, and the prompt-rendering inside `_process_one_transaction` switch to amount-keyed lookup. |
| `tests/test_store.py` | Update existing `TestSetMerchantMemory` test for new signature. Add migration test (load a config with legacy `last_processing` → assert it becomes `processings`). |
| `tests/test_transactions.py` | Update existing `TestRepeatHelpers`, `TestUpdateMerchantMemory`, `TestProcessOneTransaction` for the new shape. Add regression test: two different amounts for the same merchant both trigger Enter-repeat. |

## Non-Goals

- **Tolerance / fuzzy matching.** Exact amount only. Petrol stations etc. won't benefit. Adding tolerance is a separable future feature.
- **Auto-apply (no prompt).** Enter still required. The change is "*more* amounts can be repeated," not "amounts apply silently."
- **Bounded `processings` size.** A merchant could accumulate hundreds of entries over years; we don't prune. If it ever becomes a problem, an LRU cap (e.g., 50 entries, evicting least-recently-used) is a simple follow-up.
- **Cross-merchant amount sharing.** Memory stays per-merchant. A -R20 charge at Lifestyle on Kloof doesn't influence a -R20 charge from a different merchant.

## Implementation Notes

- **Stringify discipline:** `str(int)` always produces a canonical string (no leading zeros, no thousand separators), so `str(-50000) == "-50000"` is unambiguous. No locale dependency.
- **Migration save robustness:** if `_rebuild_indexes` mutates `self._data` but the follow-up `_save` fails (disk full, etc.), the in-memory store has `processings` but the on-disk file still has `last_processing`. Next run re-migrates. Idempotent — no corruption.
