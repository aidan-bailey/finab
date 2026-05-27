# Amount-Keyed Processings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `merchant.last_processing` (single most-recent decision) with `merchant.processings` (dict keyed by amount), so Enter-repeat fires for any historically-categorized amount, not only the last.

**Architecture:** A one-shot migration runs in `ConfigStore._rebuild_indexes` to convert any legacy `last_processing` into a single-entry `processings` dict. `ConfigStore.set_merchant_memory` swaps its `last_processing` kwarg for `processings`. Four callsites in `src/finab/transactions.py` change shape in lockstep: `_update_merchant_memory` builds the dict; `_can_repeat`, `_apply_repeat`, and the prompt preview in `_process_one_transaction` look up by `str(txn.amount)`.

**Tech Stack:** Python 3.14, pytest, `uv` package manager.

**Reference spec:** `docs/superpowers/specs/2026-05-27-amount-keyed-processings-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/finab/store.py` | Modify | Add migration in `_rebuild_indexes`. Change `set_merchant_memory` signature: `last_processing` → `processings`. |
| `src/finab/transactions.py` | Modify | `_can_repeat`, `_apply_repeat`, `_update_merchant_memory`, prompt preview in `_process_one_transaction` switch to amount-keyed lookup. |
| `tests/test_store.py` | Modify | Update existing `TestSetMerchantMemory`. Add migration test. |
| `tests/test_transactions.py` | Modify | Update existing `TestRepeatHelpers`, `TestUpdateMerchantMemory`, `TestProcessOneTransaction`. Add multi-amount regression test. |

---

## Task 1: Amount-keyed processings (single atomic change)

This task is one logical refactor that touches multiple files. The signature change to `ConfigStore.set_merchant_memory` is breaking — its only internal caller (`_update_merchant_memory`) must change in lockstep. Test files referencing the old shape are updated in the same task. Each sub-step is a TDD pair (write failing test, implement, see it pass) or a small refactor.

**Files:**
- Modify: `src/finab/store.py`
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_transactions.py`

---

- [ ] **Step 1: Write the failing migration test**

Append to `tests/test_store.py`:

```python
class TestMigrateLastProcessingToProcessings(unittest.TestCase):
    """One-shot migration: legacy merchant.last_processing becomes a
    single-entry merchant.processings on load via _rebuild_indexes."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_legacy_last_processing_migrates_to_processings(self):
        legacy = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "categories_used": {"cat-A": 3},
                    "last_processing": {
                        "amount_milliunits": -50000,
                        "parent_memo": "groceries",
                        "splits": [
                            {"category_id": "cat-A",
                             "amount_milliunits": -50000,
                             "memo": ""}
                        ],
                    },
                }
            },
        }
        self.path.write_text(json.dumps(legacy))

        store = ConfigStore(self.path)
        m = store._data["merchants"]["m-1"]

        self.assertNotIn("last_processing", m)
        self.assertIn("processings", m)
        self.assertIn("-50000", m["processings"])
        self.assertEqual(m["processings"]["-50000"]["parent_memo"], "groceries")
        self.assertEqual(
            m["processings"]["-50000"]["splits"][0]["category_id"], "cat-A"
        )

    def test_already_migrated_is_idempotent(self):
        already = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "processings": {
                        "-50000": {"parent_memo": "", "splits": []}
                    },
                }
            },
        }
        self.path.write_text(json.dumps(already))

        store = ConfigStore(self.path)
        m = store._data["merchants"]["m-1"]
        # Unchanged
        self.assertEqual(list(m["processings"].keys()), ["-50000"])
        self.assertNotIn("last_processing", m)

    def test_migration_persists_to_disk(self):
        legacy = {
            "accounts": {},
            "merchants": {
                "m-1": {
                    "id": "m-1",
                    "alias": "Spar",
                    "finwise": {},
                    "ynab": {},
                    "last_processing": {
                        "amount_milliunits": -50000,
                        "parent_memo": "",
                        "splits": [],
                    },
                }
            },
        }
        self.path.write_text(json.dumps(legacy))

        # First load triggers migration; verify it saved.
        ConfigStore(self.path)

        on_disk = json.loads(self.path.read_text())
        m = on_disk["merchants"]["m-1"]
        self.assertNotIn("last_processing", m)
        self.assertIn("processings", m)
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_store.py::TestMigrateLastProcessingToProcessings -v`
Expected: all three tests FAIL. The first two with `AssertionError: 'processings' not in m` (or `'last_processing' in m`); the third with the disk file still containing `last_processing`.

- [ ] **Step 3: Add migration to `ConfigStore._rebuild_indexes`**

In `src/finab/store.py`, modify `_rebuild_indexes` to run the migration before building the lookup dicts, and trigger a save if any merchant was migrated.

Find the existing function:

```python
    def _rebuild_indexes(self) -> None:
        self._fw_account_index: dict[str, str] = {}
        self._alias_account_index: dict[str, str] = {}
        self._fw_merchant_index: dict[str, str] = {}
        self._alias_merchant_index: dict[str, str] = {}

        for acc in self._data.get("accounts", {}).values():
            ...

        for m in self._data.get("merchants", {}).values():
            ...
```

Insert this block at the very top of `_rebuild_indexes`, before any index dicts are initialised:

```python
        # One-shot migration: legacy merchant.last_processing -> processings.
        migrated_any = False
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
                migrated_any = True
```

At the END of `_rebuild_indexes` (after all the for-loops that build the indexes), add:

```python
        if migrated_any:
            self._save()
```

(The `_save` only fires once per process — after the first load — and is idempotent on subsequent calls since `migrated_any` stays False once the data is already in the new shape.)

- [ ] **Step 4: Run migration tests, expect pass**

Run: `uv run pytest tests/test_store.py::TestMigrateLastProcessingToProcessings -v`
Expected: all three tests PASS.

- [ ] **Step 5: Update the existing `TestSetMerchantMemory` test for the new signature**

In `tests/test_store.py`, find:

```python
    def test_set_merchant_memory_writes_both_fields(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        cu = {"cat-1": 5, "cat-2": 1}
        lp = {
            "amount_milliunits": -10000,
            "parent_memo": "supermarket",
            "splits": [{"category_id": "cat-1", "amount_milliunits": -10000, "memo": ""}],
        }
        store.set_merchant_memory(m["id"], categories_used=cu, last_processing=lp)

        store2 = ConfigStore(self.path)
        found = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(found["categories_used"], cu)
        self.assertEqual(found["last_processing"], lp)
```

Replace with:

```python
    def test_set_merchant_memory_writes_both_fields(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        cu = {"cat-1": 5, "cat-2": 1}
        processings = {
            "-10000": {
                "parent_memo": "supermarket",
                "splits": [{"category_id": "cat-1",
                            "amount_milliunits": -10000, "memo": ""}],
            }
        }
        store.set_merchant_memory(m["id"], categories_used=cu, processings=processings)

        store2 = ConfigStore(self.path)
        found = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(found["categories_used"], cu)
        self.assertEqual(found["processings"], processings)
```

- [ ] **Step 6: Run, expect fail**

Run: `uv run pytest tests/test_store.py::TestSetMerchantMemory -v`
Expected: FAIL with `TypeError: ConfigStore.set_merchant_memory() got an unexpected keyword argument 'processings'`.

- [ ] **Step 7: Change `set_merchant_memory` signature**

In `src/finab/store.py`, find:

```python
    def set_merchant_memory(
        self,
        merchant_id: str,
        categories_used: dict,
        last_processing: dict,
    ) -> None:
        """Write the per-merchant categorization memory atomically."""
        m = self._data["merchants"][merchant_id]
        m["categories_used"] = dict(categories_used)
        m["last_processing"] = dict(last_processing)
        self._rebuild_indexes()
        self._save()
```

Replace with:

```python
    def set_merchant_memory(
        self,
        merchant_id: str,
        categories_used: dict,
        processings: dict,
    ) -> None:
        """Write the per-merchant categorization memory atomically.

        `processings` is a dict keyed by str(amount_milliunits) holding
        {parent_memo, splits} entries — one per distinct amount this
        merchant has been categorized for.
        """
        m = self._data["merchants"][merchant_id]
        m["categories_used"] = dict(categories_used)
        m["processings"] = dict(processings)
        self._rebuild_indexes()
        self._save()
```

- [ ] **Step 8: Run, expect pass**

Run: `uv run pytest tests/test_store.py::TestSetMerchantMemory -v`
Expected: PASS.

- [ ] **Step 9: Run the rest of the store test suite**

Run: `uv run pytest tests/test_store.py -v`
Expected: all tests PASS.

- [ ] **Step 10: Update transactions.py — `_update_merchant_memory`**

In `src/finab/transactions.py`, find:

```python
def _update_merchant_memory(store: ConfigStore, merchant: dict, txn) -> None:
    """Update the merchant's categories_used (frequency map) and
    last_processing (split structure for Enter-repeat) based on the just-
    categorized transaction. Persists via store.set_merchant_memory.

    Stringifies category_id values before storing — pickers may return
    UUID objects (from the YNAB SDK) and JSON can't serialize UUID dict
    keys."""
    def _cid_str(value):
        return str(value) if value is not None else None

    subs = list(getattr(txn, "subtransactions", []) or [])
    if subs:
        splits = [
            {
                "category_id": _cid_str(s["category_id"]),
                "amount_milliunits": s["amount"],
                "memo": s.get("memo", "") or "",
            }
            for s in subs
        ]
    else:
        splits = [
            {
                "category_id": _cid_str(txn.category_id),
                "amount_milliunits": txn.amount,
                "memo": getattr(txn, "memo", "") or "",
            }
        ]

    counts = dict(merchant.get("categories_used", {}) or {})
    for s in splits:
        cid = s["category_id"]
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    last_processing = {
        "amount_milliunits": txn.amount,
        "parent_memo": getattr(txn, "memo", "") or "",
        "splits": splits,
    }

    store.set_merchant_memory(
        merchant["id"],
        categories_used=counts,
        last_processing=last_processing,
    )
```

Replace with:

```python
def _update_merchant_memory(store: ConfigStore, merchant: dict, txn) -> None:
    """Update the merchant's categories_used (frequency map) and
    processings (dict of {str(amount): {parent_memo, splits}}) based on
    the just-categorized transaction. Persists via
    store.set_merchant_memory.

    Stringifies category_id values before storing — pickers may return
    UUID objects (from the YNAB SDK) and JSON can't serialize UUID dict
    keys."""
    def _cid_str(value):
        return str(value) if value is not None else None

    subs = list(getattr(txn, "subtransactions", []) or [])
    if subs:
        splits = [
            {
                "category_id": _cid_str(s["category_id"]),
                "amount_milliunits": s["amount"],
                "memo": s.get("memo", "") or "",
            }
            for s in subs
        ]
    else:
        splits = [
            {
                "category_id": _cid_str(txn.category_id),
                "amount_milliunits": txn.amount,
                "memo": getattr(txn, "memo", "") or "",
            }
        ]

    counts = dict(merchant.get("categories_used", {}) or {})
    for s in splits:
        cid = s["category_id"]
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    new_entry = {
        "parent_memo": getattr(txn, "memo", "") or "",
        "splits": splits,
    }
    processings = dict(merchant.get("processings", {}) or {})
    processings[str(txn.amount)] = new_entry

    store.set_merchant_memory(
        merchant["id"],
        categories_used=counts,
        processings=processings,
    )
```

- [ ] **Step 11: Update transactions.py — `_can_repeat`**

In `src/finab/transactions.py`, find:

```python
def _can_repeat(merchant: dict, txn) -> bool:
    """True iff the merchant has a last_processing whose amount equals
    txn.amount exactly. (Enter-repeat fires only on exact-amount match.)"""
    lp = merchant.get("last_processing") if merchant else None
    if not lp:
        return False
    stored = lp.get("amount_milliunits")
    current = getattr(txn, "amount", None)
    return stored is not None and current is not None and stored == current
```

Replace with:

```python
def _can_repeat(merchant: dict, txn) -> bool:
    """True iff merchant.processings has an entry for the current
    transaction's exact amount. (Enter-repeat fires only on exact-amount
    match.)"""
    if not merchant:
        return False
    processings = merchant.get("processings") or {}
    if not processings:
        return False
    amt = getattr(txn, "amount", None)
    if amt is None:
        return False
    return str(amt) in processings
```

- [ ] **Step 12: Update transactions.py — `_apply_repeat`**

In `src/finab/transactions.py`, find:

```python
def _apply_repeat(merchant: dict, txn) -> None:
    """Replay merchant.last_processing onto txn. Single-category cases set
    txn.category_id; multi-split cases set txn.subtransactions. Memos use
    fresh defaults (FinWise description for parent, empty for splits) so
    the user doesn't inherit stale per-transaction notes."""
    lp = merchant["last_processing"]
    splits = lp.get("splits", []) or []
    if len(splits) == 1:
        txn.category_id = splits[0]["category_id"]
        txn.subtransactions = []
    else:
        txn.category_id = None
        txn.subtransactions = [
            {
                "category_id": s["category_id"],
                "amount": s["amount_milliunits"],
                "memo": "",  # fresh default per spec
            }
            for s in splits
        ]
    # Parent memo: keep whatever txn.memo already is (it's the FinWise
    # description by default after Transaction.from_finwise).
```

Replace with:

```python
def _apply_repeat(merchant: dict, txn) -> None:
    """Replay merchant.processings[str(txn.amount)] onto txn. Single-category
    cases set txn.category_id; multi-split cases set txn.subtransactions.
    Memos use fresh defaults (FinWise description for parent, empty for
    splits) so the user doesn't inherit stale per-transaction notes."""
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
                "memo": "",  # fresh default per spec
            }
            for s in splits
        ]
    # Parent memo: keep whatever txn.memo already is (it's the FinWise
    # description by default after Transaction.from_finwise).
```

- [ ] **Step 13: Update transactions.py — `_process_one_transaction` preview**

In `src/finab/transactions.py`, find the preview block inside `_process_one_transaction`:

```python
    repeat_available = _can_repeat(merchant, txn)
    if repeat_available:
        lp = merchant["last_processing"]
        if len(lp["splits"]) == 1:
            cat_id = lp["splits"][0]["category_id"]
            cat_name = _category_name(ynab_categories, cat_id) or "?"
            preview = f"{cat_name} {amount_str}"
        else:
            preview = f"split into {len(lp['splits'])} categories"
        print()
        print(f"  {_bold('[Enter]')} to repeat last: {preview}")
```

Replace with:

```python
    repeat_available = _can_repeat(merchant, txn)
    if repeat_available:
        entry = merchant["processings"][str(txn.amount)]
        if len(entry["splits"]) == 1:
            cat_id = entry["splits"][0]["category_id"]
            cat_name = _category_name(ynab_categories, cat_id) or "?"
            preview = f"{cat_name} {amount_str}"
        else:
            preview = f"split into {len(entry['splits'])} categories"
        print()
        print(f"  {_bold('[Enter]')} to repeat last: {preview}")
```

- [ ] **Step 14: Update existing `TestRepeatHelpers` tests in `tests/test_transactions.py`**

In `tests/test_transactions.py`, find the existing `TestRepeatHelpers` class:

```python
class TestRepeatHelpers(unittest.TestCase):
    def test_can_repeat_when_exact_amount_match(self):
        merchant = {"last_processing": {"amount_milliunits": -1000, "splits": []}}
        txn = MagicMock(amount=-1000)
        self.assertTrue(_can_repeat(merchant, txn))

    def test_can_not_repeat_when_amount_differs(self):
        merchant = {"last_processing": {"amount_milliunits": -1000, "splits": []}}
        txn = MagicMock(amount=-2000)
        self.assertFalse(_can_repeat(merchant, txn))

    def test_can_not_repeat_when_no_last_processing(self):
        self.assertFalse(_can_repeat({}, MagicMock(amount=-1000)))

    def test_apply_repeat_single_category(self):
        lp = {
            "amount_milliunits": -1000,
            "parent_memo": "",
            "splits": [
                {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
            ],
        }
        merchant = {"last_processing": lp}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.category_id, "cat-A")
        self.assertEqual(txn.subtransactions, [])
        # Memo stays as the FinWise description (default), per spec.
        self.assertEqual(txn.memo, "finwise desc")

    def test_apply_repeat_split(self):
        lp = {
            "amount_milliunits": -1000,
            "parent_memo": "",
            "splits": [
                {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
                {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
            ],
        }
        merchant = {"last_processing": lp}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertIsNone(txn.category_id)
        self.assertEqual(len(txn.subtransactions), 2)
        self.assertEqual(txn.subtransactions[0]["category_id"], "cat-A")
        self.assertEqual(txn.subtransactions[0]["amount"], -600)
        self.assertEqual(txn.subtransactions[1]["amount"], -400)
```

Replace with:

```python
class TestRepeatHelpers(unittest.TestCase):
    def test_can_repeat_when_amount_in_processings(self):
        merchant = {"processings": {"-1000": {"parent_memo": "", "splits": []}}}
        txn = MagicMock(amount=-1000)
        self.assertTrue(_can_repeat(merchant, txn))

    def test_can_repeat_finds_any_matching_amount(self):
        # Two distinct amounts; either current matches -> repeat available.
        merchant = {"processings": {
            "-1000": {"parent_memo": "", "splits": []},
            "-5000": {"parent_memo": "", "splits": []},
        }}
        self.assertTrue(_can_repeat(merchant, MagicMock(amount=-1000)))
        self.assertTrue(_can_repeat(merchant, MagicMock(amount=-5000)))

    def test_can_not_repeat_when_amount_not_in_processings(self):
        merchant = {"processings": {"-1000": {"parent_memo": "", "splits": []}}}
        self.assertFalse(_can_repeat(merchant, MagicMock(amount=-2000)))

    def test_can_not_repeat_when_no_processings(self):
        self.assertFalse(_can_repeat({}, MagicMock(amount=-1000)))
        self.assertFalse(_can_repeat({"processings": {}}, MagicMock(amount=-1000)))

    def test_apply_repeat_single_category(self):
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ],
            }
        }}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertEqual(txn.category_id, "cat-A")
        self.assertEqual(txn.subtransactions, [])
        # Memo stays as the FinWise description (default), per spec.
        self.assertEqual(txn.memo, "finwise desc")

    def test_apply_repeat_split(self):
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -600, "memo": ""},
                    {"category_id": "cat-B", "amount_milliunits": -400, "memo": ""},
                ],
            }
        }}
        txn = MagicMock(amount=-1000, memo="finwise desc")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        self.assertIsNone(txn.category_id)
        self.assertEqual(len(txn.subtransactions), 2)
        self.assertEqual(txn.subtransactions[0]["category_id"], "cat-A")
        self.assertEqual(txn.subtransactions[0]["amount"], -600)
        self.assertEqual(txn.subtransactions[1]["amount"], -400)

    def test_apply_repeat_picks_correct_entry_among_many(self):
        """Multiple amounts in processings — apply uses the one matching
        txn.amount specifically."""
        merchant = {"processings": {
            "-1000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-A", "amount_milliunits": -1000, "memo": ""}
                ],
            },
            "-5000": {
                "parent_memo": "",
                "splits": [
                    {"category_id": "cat-B", "amount_milliunits": -5000, "memo": ""}
                ],
            },
        }}
        txn = MagicMock(amount=-5000, memo="")
        txn.subtransactions = []
        _apply_repeat(merchant, txn)
        # Picked the -5000 entry, not the -1000 entry.
        self.assertEqual(txn.category_id, "cat-B")
```

- [ ] **Step 15: Update `TestUpdateMerchantMemory` tests in `tests/test_transactions.py`**

Find the existing `TestUpdateMerchantMemory` class:

```python
class TestUpdateMerchantMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.merchant = self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _single_txn(self, amount, cat_id, memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = cat_id
        t.subtransactions = []
        t.memo = memo
        return t

    def _split_txn(self, amount, subs, parent_memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = None
        t.subtransactions = subs
        t.memo = parent_memo
        return t

    def test_single_category_updates_count_and_last(self):
        txn = self._single_txn(-50000, "cat-A", memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1})
        self.assertEqual(m["last_processing"]["amount_milliunits"], -50000)
        self.assertEqual(m["last_processing"]["parent_memo"], "receipt")
        self.assertEqual(len(m["last_processing"]["splits"]), 1)
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["last_processing"]["splits"][0]["amount_milliunits"], -50000)

    def test_split_increments_each_category(self):
        subs = [
            {"category_id": "cat-A", "amount": -30000, "memo": "fuel"},
            {"category_id": "cat-B", "amount": -20000, "memo": "snacks"},
        ]
        txn = self._split_txn(-50000, subs, parent_memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})
        self.assertEqual(len(m["last_processing"]["splits"]), 2)
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["last_processing"]["splits"][0]["amount_milliunits"], -30000)
        self.assertEqual(m["last_processing"]["splits"][0]["memo"], "fuel")

    def test_increment_runs_cumulatively(self):
        # First processing
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        # Reload and do another
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-2000, "cat-A"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"]["cat-A"], 2)
```

Replace with:

```python
class TestUpdateMerchantMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.merchant = self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _single_txn(self, amount, cat_id, memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = cat_id
        t.subtransactions = []
        t.memo = memo
        return t

    def _split_txn(self, amount, subs, parent_memo=""):
        t = MagicMock()
        t.amount = amount
        t.category_id = None
        t.subtransactions = subs
        t.memo = parent_memo
        return t

    def test_single_category_writes_processings_keyed_by_amount(self):
        txn = self._single_txn(-50000, "cat-A", memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1})
        self.assertIn("-50000", m["processings"])
        entry = m["processings"]["-50000"]
        self.assertEqual(entry["parent_memo"], "receipt")
        self.assertEqual(len(entry["splits"]), 1)
        self.assertEqual(entry["splits"][0]["category_id"], "cat-A")
        self.assertEqual(entry["splits"][0]["amount_milliunits"], -50000)

    def test_split_increments_each_category(self):
        subs = [
            {"category_id": "cat-A", "amount": -30000, "memo": "fuel"},
            {"category_id": "cat-B", "amount": -20000, "memo": "snacks"},
        ]
        txn = self._split_txn(-50000, subs, parent_memo="receipt")
        _update_merchant_memory(self.store, self.merchant, txn)

        store2 = ConfigStore(self.path)
        m = store2.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})
        entry = m["processings"]["-50000"]
        self.assertEqual(len(entry["splits"]), 2)
        self.assertEqual(entry["splits"][0]["category_id"], "cat-A")
        self.assertEqual(entry["splits"][0]["amount_milliunits"], -30000)
        self.assertEqual(entry["splits"][0]["memo"], "fuel")

    def test_distinct_amounts_accumulate_separate_entries(self):
        """Categorizing -1000 then -2000 for the same merchant produces
        TWO entries in processings, one per amount."""
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-2000, "cat-B"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(set(m["processings"].keys()), {"-1000", "-2000"})
        self.assertEqual(m["processings"]["-1000"]["splits"][0]["category_id"], "cat-A")
        self.assertEqual(m["processings"]["-2000"]["splits"][0]["category_id"], "cat-B")
        # categories_used reflects both
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})

    def test_same_amount_recategorize_overwrites(self):
        """Categorizing -1000 as cat-A, then re-categorizing -1000 as cat-B,
        replaces the entry for -1000 (most recent wins per amount).
        categories_used reflects both uses cumulatively."""
        _update_merchant_memory(
            self.store, self.merchant, self._single_txn(-1000, "cat-A")
        )
        self.store = ConfigStore(self.path)
        merchant2 = self.store.merchant_by_finwise_id("fw-spar")
        _update_merchant_memory(self.store, merchant2, self._single_txn(-1000, "cat-B"))

        store3 = ConfigStore(self.path)
        m = store3.merchant_by_finwise_id("fw-spar")
        self.assertEqual(list(m["processings"].keys()), ["-1000"])
        self.assertEqual(m["processings"]["-1000"]["splits"][0]["category_id"], "cat-B")
        # Both decisions counted
        self.assertEqual(m["categories_used"], {"cat-A": 1, "cat-B": 1})
```

- [ ] **Step 16: Update `TestProcessOneTransaction` Enter-repeat tests**

Find the existing tests inside `TestProcessOneTransaction` that seed `last_processing`:

```python
    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["c", "1", ""])
    def test_interactive_single_category(self, _input, _stdout):
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            last_processing={
                "amount_milliunits": -9999,  # mismatched so no Enter-repeat
                "parent_memo": "",
                "splits": [{"category_id": "c-petrol",
                            "amount_milliunits": -9999, "memo": ""}],
            },
        )
```

Replace ALL `set_merchant_memory(..., last_processing=...)` calls in `TestProcessOneTransaction` (and anywhere else in `tests/test_transactions.py`) with the new shape. Search the file with this grep first:

```bash
grep -n "last_processing" tests/test_transactions.py
```

Each occurrence converts as follows. Wherever you see:

```python
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            last_processing={
                "amount_milliunits": -9999,
                "parent_memo": "",
                "splits": [{"category_id": "c-petrol",
                            "amount_milliunits": -9999, "memo": ""}],
            },
        )
```

Replace with:

```python
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            processings={
                "-9999": {
                    "parent_memo": "",
                    "splits": [{"category_id": "c-petrol",
                                "amount_milliunits": -9999, "memo": ""}],
                }
            },
        )
```

(Adjust the inner amount and category to match each test's intent — the conversion is purely structural.)

After all replacements, run `grep -n "last_processing" tests/test_transactions.py` again. Expected: no matches.

- [ ] **Step 17: Update `TestMemoryGateOnMissingDecision` test**

This test in `tests/test_transactions.py` seeds `last_processing` to assert it's NOT overwritten when a transfer is processed. Update it to seed `processings` instead.

Find:

```python
        # Seed a pre-existing last_processing on the merchant — we want to
        # verify it's NOT overwritten.
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"cat-existing": 3},
            last_processing={
                "amount_milliunits": -1234,
                "parent_memo": "previous",
                "splits": [{"category_id": "cat-existing",
                            "amount_milliunits": -1234, "memo": ""}],
            },
        )
```

Replace with:

```python
        # Seed a pre-existing processings entry on the merchant — we want
        # to verify it's NOT overwritten.
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"cat-existing": 3},
            processings={
                "-1234": {
                    "parent_memo": "previous",
                    "splits": [{"category_id": "cat-existing",
                                "amount_milliunits": -1234, "memo": ""}],
                }
            },
        )
```

Then find the test's assertions block:

```python
        m = self.store.merchant_by_finwise_id("fw-xfer")
        # The pre-existing memory must remain untouched.
        self.assertEqual(m["categories_used"], {"cat-existing": 3})
        self.assertEqual(m["last_processing"]["amount_milliunits"], -1234)
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], "cat-existing")
```

Replace with:

```python
        m = self.store.merchant_by_finwise_id("fw-xfer")
        # The pre-existing memory must remain untouched.
        self.assertEqual(m["categories_used"], {"cat-existing": 3})
        self.assertIn("-1234", m["processings"])
        self.assertEqual(
            m["processings"]["-1234"]["splits"][0]["category_id"], "cat-existing"
        )
```

- [ ] **Step 18: Update `TestMerchantMemoryStringifiesCategoryId` assertion**

Find this assertion in `tests/test_transactions.py`:

```python
        self.assertEqual(m["last_processing"]["splits"][0]["category_id"], str(uuid_cat))
```

Replace with:

```python
        # Single-category txn at amount -44000 -> processings["-44000"]
        self.assertEqual(
            m["processings"]["-44000"]["splits"][0]["category_id"], str(uuid_cat)
        )
```

- [ ] **Step 19: Run the full test suite**

Run: `uv run pytest -q`
Expected: all tests PASS. Test count should be one or two higher than before (we added `TestMigrateLastProcessingToProcessings` with 3 tests and two new tests in `TestRepeatHelpers` and `TestUpdateMerchantMemory`).

If anything fails, the most likely cause is a remaining `last_processing` reference. Run `grep -rn "last_processing" src/ tests/` — every match should now be (a) gone, or (b) inside the migration block in `store.py`.

- [ ] **Step 20: Commit**

```bash
git add src/finab/store.py src/finab/transactions.py tests/test_store.py tests/test_transactions.py
git commit -m "feat(store, transactions): amount-keyed merchant.processings

Replaces merchant.last_processing (single most-recent decision) with
merchant.processings (dict keyed by str(amount_milliunits)). Enter-repeat
now fires when the current transaction's amount matches ANY historically-
categorized amount for that merchant, not only the last one.

ConfigStore._rebuild_indexes runs a one-shot, idempotent migration:
existing last_processing entries become single-key processings dicts.
Saves once when the migration first fires; subsequent loads find nothing
to migrate and skip the save.

ConfigStore.set_merchant_memory signature: last_processing -> processings.
Single internal caller (_update_merchant_memory) updated in lockstep.

_can_repeat, _apply_repeat, and the prompt preview in
_process_one_transaction switch to amount-keyed lookup
(merchant.processings[str(txn.amount)]).

Spec: docs/superpowers/specs/2026-05-27-amount-keyed-processings-design.md

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## Self-Review Checklist (done)

**Spec coverage:**
- Schema change (last_processing → processings) → Steps 7, 10
- Migration in `_rebuild_indexes` → Step 3, tested in Steps 1–4
- `set_merchant_memory` signature change → Steps 7, 8
- `_can_repeat` reads processings → Step 11, tested in Step 14
- `_apply_repeat` reads processings → Step 12, tested in Step 14 (incl. multi-amount regression)
- `_update_merchant_memory` writes processings → Step 10, tested in Step 15 (incl. distinct-amount accumulation and same-amount overwrite)
- Prompt preview reads from processings → Step 13
- Migration persists to disk → Step 3 (the `if migrated_any: self._save()` line), tested in Step 1's `test_migration_persists_to_disk`

**Placeholder scan:** No TBDs, no "similar to" references, no "add appropriate error handling". All code is shown in full. ✓

**Type / method consistency:** `processings: dict`, keyed by `str(amount_milliunits)`, values shape `{parent_memo: str, splits: list}`. Used identically in all callsites. ✓
