# Phase 3 (Transaction Sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Phase 3 — `sync_transactions` — with per-merchant category memory, splits, new-category creation, memos, and a manual flush queue. Delete the legacy transaction pipeline.

**Architecture:** A new `src/finab/transactions.py` module owns the entire flow. It uses the `ConfigStore` from Phases 1 & 2, drives all interactive prompts itself, and pushes to YNAB via the existing `YNABClient` (extended with `create_category` and `create_category_group`). Pending transactions queue in memory and flush on demand (`f` command), at end of run (`finally`), or after Ctrl+C confirmation. Roughly 700 lines of legacy code are removed from `main.py` and `config.py`.

**Tech Stack:** Python 3.14, `ynab>=4.1.0` (official SDK), pytest, `uv` package manager.

**Reference spec:** `docs/superpowers/specs/2026-05-26-phase3-transactions-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/finab/transactions.py` | **Create** | Phase 3 orchestrator (`sync_transactions`), `_PendingQueue`, auto-paths, categorization helpers, picker, splits, memo prompts, merchant memory updates. |
| `src/finab/store.py` | Modify | Add `set_merchant_memory(merchant_id, categories_used, last_processing)`. |
| `src/finab/ynab_client.py` | Modify | Add `create_category`, `create_category_group`, `get_category_groups_with_categories`. |
| `src/finab/main.py` | Modify | Import `sync_transactions` from `transactions.py`. Delete legacy `process_payee_aliases`, `process_categories`, `collect_split_subtransactions`, `_build_cache`, `_apply_cache`, `map_accounts`, `fetch_transactions`, `sync_changes_to_ynab`, the old `sync_transactions`, and `normalize_payee_for_matching`. Update `main()` to call new sync. |
| `src/finab/config.py` | Modify | Delete `load_payee_rules`, `save_payee_rules`, `load_category_rules`, `save_category_rules`, `load_cache`, `save_cache`, `clear_cache`, `CACHE_FILE`. |
| `tests/test_transactions.py` | **Create** | Unit tests for `_PendingQueue`, auto-paths, picker, splits, memo, memory updates, sort key. |
| `tests/test_ynab_categories.py` | **Create** | Tests for `create_category` and `create_category_group`. |
| `tests/test_sync_transactions.py` | Replace | Integration test for the full `sync_transactions` flow against mocked clients and a real `ConfigStore`. |

---

## Task 1: `ConfigStore.set_merchant_memory`

**Files:**
- Modify: `src/finab/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Append failing test to `tests/test_store.py`**

Add at the end of the file:

```python
class TestSetMerchantMemory(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

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

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_store.py::TestSetMerchantMemory -v`
Expected: FAIL — `AttributeError: 'ConfigStore' object has no attribute 'set_merchant_memory'`.

- [ ] **Step 3: Add the method to `ConfigStore`**

In `src/finab/store.py`, add inside the `ConfigStore` class (near `set_merchant_ynab_record`):

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

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_store.py -v`
Expected: All store tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): add set_merchant_memory for Phase 3 categorization

Writes the merchant's categories_used (frequency map) and
last_processing (split structure for Enter-repeat) atomically."
```

---

## Task 2: `YNABClient.create_category` + `create_category_group`

**Files:**
- Modify: `src/finab/ynab_client.py`
- Create: `tests/test_ynab_categories.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ynab_categories.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

from finab.ynab_client import YNABClient


class TestCreateCategoryGroup(unittest.TestCase):
    @patch("finab.ynab_client.CategoriesApi")
    def test_create_category_group_calls_sdk(self, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_response = MagicMock()
        mock_response.data.category_group = MagicMock(id="grp-1", name="Pets")
        mock_api.create_category_group.return_value = mock_response

        client = YNABClient(api_key="test")
        result = client.create_category_group("bid", "Pets")

        mock_api.create_category_group.assert_called_once()
        call_args = mock_api.create_category_group.call_args
        self.assertEqual(call_args.args[0], "bid")
        wrapper = call_args.args[1]
        self.assertEqual(wrapper.category_group.name, "Pets")
        self.assertEqual(result.id, "grp-1")


class TestCreateCategory(unittest.TestCase):
    @patch("finab.ynab_client.CategoriesApi")
    def test_create_category_calls_sdk(self, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        mock_response = MagicMock()
        mock_response.data.category = MagicMock(
            id="cat-1", name="Pet Supplies", category_group_id="grp-1"
        )
        mock_api.create_category.return_value = mock_response

        client = YNABClient(api_key="test")
        result = client.create_category("bid", "Pet Supplies", "grp-1")

        mock_api.create_category.assert_called_once()
        call_args = mock_api.create_category.call_args
        self.assertEqual(call_args.args[0], "bid")
        wrapper = call_args.args[1]
        self.assertEqual(wrapper.category.name, "Pet Supplies")
        self.assertEqual(wrapper.category.category_group_id, "grp-1")
        self.assertEqual(result.id, "cat-1")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_ynab_categories.py -v`
Expected: FAIL — `AttributeError: 'YNABClient' object has no attribute 'create_category_group'`.

- [ ] **Step 3: Add imports and methods to `ynab_client.py`**

In the `from ynab import (...)` block, add:

```python
    SaveCategory,
    SaveCategoryGroup,
    PostCategoryWrapper,
    PostCategoryGroupWrapper,
```

Then add two methods to `YNABClient` (near `create_payee`):

```python
    def create_category_group(self, budget_id: str, name: str) -> Any:
        """Create a new category group. Returns the created CategoryGroup."""
        categories_api = CategoriesApi(self.api_client)
        wrapper = PostCategoryGroupWrapper(category_group=SaveCategoryGroup(name=name))
        response = categories_api.create_category_group(budget_id, wrapper)
        return response.data.category_group

    def create_category(
        self, budget_id: str, name: str, category_group_id: str
    ) -> Any:
        """Create a new category in the given group. Returns the created Category."""
        categories_api = CategoriesApi(self.api_client)
        wrapper = PostCategoryWrapper(
            category=SaveCategory(name=name, category_group_id=category_group_id)
        )
        response = categories_api.create_category(budget_id, wrapper)
        return response.data.category
```

If `CategoriesApi` isn't already imported at the top, add `CategoriesApi` to the `from ynab import (...)` block.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_ynab_categories.py -v`
Expected: PASS (2 tests).

Then: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/ynab_client.py tests/test_ynab_categories.py
git commit -m "feat(ynab): add create_category and create_category_group

Wraps the official SDK's create_category and create_category_group
methods for Phase 3's new-category creation flow."
```

---

## Task 3: `YNABClient.get_category_groups_with_categories`

**Files:**
- Modify: `src/finab/ynab_client.py`
- Modify: `tests/test_ynab_categories.py`

- [ ] **Step 1: Append failing test**

Add to `tests/test_ynab_categories.py`:

```python
class TestGetCategoryGroupsWithCategories(unittest.TestCase):
    @patch("finab.ynab_client.CategoriesApi")
    def test_returns_groups_with_nested_categories(self, mock_api_cls):
        mock_api = MagicMock()
        mock_api_cls.return_value = mock_api
        grp1 = MagicMock(id="g1", name="Bills", categories=[MagicMock(id="c1")])
        grp2 = MagicMock(id="g2", name="Fun", categories=[MagicMock(id="c2")])
        mock_response = MagicMock()
        mock_response.data.category_groups = [grp1, grp2]
        mock_api.get_categories.return_value = mock_response

        client = YNABClient(api_key="test")
        result = client.get_category_groups_with_categories("bid")

        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].name, "Bills")
        self.assertEqual(result[0].categories[0].id, "c1")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_ynab_categories.py::TestGetCategoryGroupsWithCategories -v`
Expected: FAIL.

- [ ] **Step 3: Add the method to `YNABClient`**

In `src/finab/ynab_client.py`, near `get_categories`:

```python
    def get_category_groups_with_categories(self, budget_id: str) -> List[Any]:
        """Returns category groups with nested categories (YNAB's native shape).
        Used by the Phase 3 'pick from full list' UI."""
        categories_api = CategoriesApi(self.api_client)
        response = categories_api.get_categories(budget_id)
        return response.data.category_groups
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_ynab_categories.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/ynab_client.py tests/test_ynab_categories.py
git commit -m "feat(ynab): add get_category_groups_with_categories

Returns YNAB's nested group->categories structure for the Phase 3
category picker."
```

---

## Task 4: `transactions.py` scaffold + `_PendingQueue`

**Files:**
- Create: `src/finab/transactions.py`
- Create: `tests/test_transactions.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_transactions.py`:

```python
import unittest
from unittest.mock import MagicMock

from finab.transactions import _PendingQueue


class TestPendingQueue(unittest.TestCase):
    def test_starts_empty(self):
        q = _PendingQueue()
        self.assertEqual(q.count(), 0)

    def test_add_routes_by_ynab_id_presence(self):
        q = _PendingQueue()
        create_txn = MagicMock(ynab_id=None)
        update_txn = MagicMock(ynab_id="yn-existing")
        q.add(create_txn)
        q.add(update_txn)
        self.assertEqual(q.count(), 2)
        self.assertEqual(len(q.creates), 1)
        self.assertEqual(len(q.updates), 1)

    def test_flush_calls_both_apis_and_clears(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        q.add(MagicMock(ynab_id="yn-1"))
        client = MagicMock()
        ok = q.flush(client, "bid")
        self.assertTrue(ok)
        client.create_transactions.assert_called_once()
        client.update_transactions.assert_called_once()
        self.assertEqual(q.count(), 0)

    def test_flush_with_only_creates(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        ok = q.flush(client, "bid")
        self.assertTrue(ok)
        client.create_transactions.assert_called_once()
        client.update_transactions.assert_not_called()

    def test_flush_failure_keeps_queue(self):
        q = _PendingQueue()
        q.add(MagicMock(ynab_id=None))
        client = MagicMock()
        client.create_transactions.side_effect = RuntimeError("network")
        ok = q.flush(client, "bid")
        self.assertFalse(ok)
        self.assertEqual(q.count(), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.transactions'`.

- [ ] **Step 3: Create the scaffold**

Create `src/finab/transactions.py`:

```python
"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
from datetime import date
from typing import Any, Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore, normalize_alias


class _PendingQueue:
    """Holds categorized-but-not-yet-pushed transactions. Flushed on demand
    via the `f` command, at end of run, or after Ctrl+C confirmation."""

    def __init__(self):
        self.creates: list = []
        self.updates: list = []

    def count(self) -> int:
        return len(self.creates) + len(self.updates)

    def add(self, txn) -> None:
        if getattr(txn, "ynab_id", None):
            self.updates.append(txn)
        else:
            self.creates.append(txn)

    def flush(self, ynab_client: YNABClient, budget_id: str) -> bool:
        """Push all pending transactions in two batched calls. Returns True
        if both succeed (queue clears). On any exception, returns False and
        keeps the queue for retry."""
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


def sync_transactions(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
) -> None:
    """Phase 3 entry point. Stub for now; populated by later tasks."""
    raise NotImplementedError("sync_transactions wired in later tasks")
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): scaffold module with _PendingQueue

New Phase 3 module. _PendingQueue routes transactions by ynab_id
presence (new vs existing), flushes in batched create/update calls,
and retains state on failure for retry."
```

---

## Task 5: Auto-path helpers (`_is_inflow`, `_is_transfer`, `_find_inflow_category`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import (
    _is_inflow,
    _is_transfer,
    _find_inflow_category,
)


class TestAutoPathHelpers(unittest.TestCase):
    def test_is_inflow_positive(self):
        self.assertTrue(_is_inflow(MagicMock(amount=1000)))
        self.assertFalse(_is_inflow(MagicMock(amount=-1000)))
        self.assertFalse(_is_inflow(MagicMock(amount=0)))

    def test_is_transfer_when_merchant_has_transfer_account_id(self):
        m = {"ynab": {"id": "yn-tp", "transfer_account_id": "yn-acc-1"}}
        self.assertTrue(_is_transfer(m))

    def test_is_transfer_false_when_missing(self):
        self.assertFalse(_is_transfer({"ynab": {"id": "yn-p"}}))
        self.assertFalse(_is_transfer({"ynab": {}}))
        self.assertFalse(_is_transfer(None))

    def test_find_inflow_category_prefers_ready_to_assign(self):
        cats = [
            MagicMock(id="c1", name="Inflow: To be Budgeted", hidden=False, deleted=False),
            MagicMock(id="c2", name="Inflow: Ready to Assign", hidden=False, deleted=False),
        ]
        self.assertEqual(_find_inflow_category(cats), "c2")

    def test_find_inflow_category_skips_hidden_or_deleted(self):
        cats = [
            MagicMock(id="c1", name="Inflow: Ready to Assign", hidden=True, deleted=False),
            MagicMock(id="c2", name="Inflow: Ready to Assign", hidden=False, deleted=True),
        ]
        self.assertIsNone(_find_inflow_category(cats))

    def test_find_inflow_category_returns_none_when_absent(self):
        cats = [MagicMock(id="c1", name="Groceries", hidden=False, deleted=False)]
        self.assertIsNone(_find_inflow_category(cats))
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestAutoPathHelpers -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add helpers to `transactions.py`**

Add near the top, before `_PendingQueue`:

```python
# Names YNAB might use for the inflow category. Checked in this order.
_INFLOW_CATEGORY_NAMES = (
    "inflow: ready to assign",
    "ready to assign",
    "inflow: to be budgeted",
    "to be budgeted",
)


def _is_inflow(txn) -> bool:
    """A positive amount on a YNAB transaction is an inflow."""
    return getattr(txn, "amount", 0) > 0


def _is_transfer(merchant: Optional[dict]) -> bool:
    """A merchant whose YNAB record carries a transfer_account_id is a
    transfer payee — the transaction is a transfer to/from one of the
    user's own accounts."""
    if not merchant:
        return False
    return merchant.get("ynab", {}).get("transfer_account_id") is not None


def _find_inflow_category(categories) -> Optional[str]:
    """Find the YNAB category id for 'Inflow: Ready to Assign' (or its
    legacy variants). Returns the id of the first matching, non-hidden,
    non-deleted category; or None if none exists."""
    by_name = {}
    for c in categories:
        if getattr(c, "hidden", False) or getattr(c, "deleted", False):
            continue
        name = getattr(c, "name", "")
        by_name[name.lower()] = c
    for candidate in _INFLOW_CATEGORY_NAMES:
        c = by_name.get(candidate)
        if c is not None:
            return c.id
    return None
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): auto-path detection helpers

_is_inflow / _is_transfer / _find_inflow_category encode the three
'no prompt needed' branches: positive amounts route to Ready to
Assign, transfer-payee merchants get linked without category, and
no-merchant transactions go uncategorized."
```

---

## Task 6: Refactor `merge_and_filter_transactions` to use store

The existing `merge_and_filter_transactions` in `main.py` does FW->YNAB account mapping via the old `load_aliases` shim and includes a fuzzy-match migration fallback that's no longer needed. Move it to `transactions.py` and simplify.

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing test**

Add to `tests/test_transactions.py`:

```python
from datetime import date as _date
import tempfile
from pathlib import Path

from finab.store import ConfigStore
from finab.transactions import merge_and_filter_transactions


class TestMergeAndFilter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store = ConfigStore(self.path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, import_id, account_id, amount, payee_name="X"):
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.date = _date(2026, 5, 20)
        t.payee_name = payee_name
        t.ynab_id = None
        t.category_id = None
        return t

    def _ynab_txn(self, id, import_id, amount, category_id=None):
        t = MagicMock()
        t.id = id
        t.import_id = import_id
        t.amount = amount
        t.category_id = category_id
        t.deleted = False
        t.transfer_account_id = None
        return t

    def test_skips_fw_with_unknown_account(self):
        from finab.config import load_import_id_offset
        # Account fw-OTHER is not in the store
        fw_txns = [self._fw_txn("fw-tx-1", "fw-OTHER", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store)
        self.assertEqual(result, [])

    def test_maps_account_via_store(self):
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        result = merge_and_filter_transactions(fw_txns, [], self.store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].account_id, "yn-acc")

    def test_skips_already_categorized_match(self):
        from finab.config import load_import_id_offset
        from finab.main import generate_import_id
        offset = load_import_id_offset()
        hashed = generate_import_id("fw-tx-1", offset)
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", hashed, -1000, category_id="cat-X")]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store)
        # Already categorized in YNAB -> skipped
        self.assertEqual(result, [])

    def test_links_uncategorized_ynab_match_for_update(self):
        from finab.config import load_import_id_offset
        from finab.main import generate_import_id
        offset = load_import_id_offset()
        hashed = generate_import_id("fw-tx-1", offset)
        fw_txns = [self._fw_txn("fw-tx-1", "fw-acc", -1000)]
        ynab_txns = [self._ynab_txn("yn-tx-1", hashed, -1000, category_id=None)]
        result = merge_and_filter_transactions(fw_txns, ynab_txns, self.store)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].ynab_id, "yn-tx-1")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestMergeAndFilter -v`
Expected: FAIL — `ImportError: cannot import name 'merge_and_filter_transactions' from 'finab.transactions'`.

- [ ] **Step 3: Add `merge_and_filter_transactions` to `transactions.py`**

Below the auto-path helpers, add:

```python
def merge_and_filter_transactions(fw_transactions, ynab_transactions, store: ConfigStore) -> list:
    """Map FinWise accounts to YNAB account ids via the store, dedup against
    existing YNAB transactions by hashed import_id, and skip ones already
    categorized in YNAB. Returns the list of FinWise transactions needing
    processing. Each returned transaction has:
      - account_id rewritten to the YNAB account id
      - import_id rewritten to the hashed form
      - ynab_id set if a matching uncategorized YNAB transaction was found
        (so the caller knows to PATCH instead of POST)
    """
    from finab.main import generate_import_id  # local import to avoid cycle
    from finab.config import load_import_id_offset

    offset = load_import_id_offset()

    ynab_by_import_id = {}
    for txn in ynab_transactions:
        if getattr(txn, "import_id", None):
            ynab_by_import_id[txn.import_id] = txn

    out = []
    matched_ynab_ids = set()
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue

        hashed_id = generate_import_id(fw_txn.import_id, offset)
        fw_txn.import_id = hashed_id

        ynab_match = ynab_by_import_id.get(hashed_id)
        if ynab_match and ynab_match.id not in matched_ynab_ids:
            matched_ynab_ids.add(ynab_match.id)
            if getattr(ynab_match, "deleted", False):
                continue
            if ynab_match.category_id:
                # Already categorized — preserve user's manual YNAB work.
                continue
            fw_txn.ynab_id = ynab_match.id
            fw_txn.category_id = None

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): merge_and_filter rewritten against ConfigStore

Replaces the legacy main.py version that used the load_aliases shim
and a fuzzy-match migration fallback. New version: account mapping
via store.account_by_finwise_id, hashed import_id matching only,
skip already-categorized YNAB transactions."
```

---

## Task 7: Single-category picker (`_pick_category`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from unittest.mock import patch
from finab.transactions import _pick_category


class TestPickCategory(unittest.TestCase):
    def _category(self, cid, name, group_id="g1", hidden=False, deleted=False):
        c = MagicMock()
        c.id = cid
        c.name = name
        c.category_group_id = group_id
        c.hidden = hidden
        c.deleted = deleted
        return c

    def _category_group(self, gid, name, categories):
        g = MagicMock()
        g.id = gid
        g.name = name
        g.categories = categories
        return g

    def test_pick_from_used_by_number(self):
        merchant = {"categories_used": {"c-groceries": 47, "c-snacks": 3}}
        categories = [
            self._category("c-groceries", "Groceries"),
            self._category("c-snacks", "Snacks"),
        ]
        with patch("builtins.input", return_value="1"):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        # 47x is most-used, sorts first
        self.assertEqual(result, "c-groceries")

    def test_returns_none_when_back(self):
        merchant = {"categories_used": {"c-groceries": 1}}
        categories = [self._category("c-groceries", "Groceries")]
        with patch("builtins.input", return_value="b"):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        self.assertIsNone(result)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    def test_out_of_range_reprompts(self, _stdout):
        merchant = {"categories_used": {"c-groceries": 1}}
        categories = [self._category("c-groceries", "Groceries")]
        # 99 is out of range; reprompt; then 1 picks the only used category.
        with patch("builtins.input", side_effect=["99", "1"]):
            result = _pick_category(merchant, categories, [], MagicMock(), "bid")
        self.assertEqual(result, "c-groceries")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestPickCategory -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement the picker**

Add to `transactions.py` (after the auto-path helpers):

```python
import sys


# --- Color helpers (mirror main.py; kept local to avoid cross-module imports). ---
def _color(code: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"

def _bold(s: str) -> str:   return _color("1", s)
def _dim(s: str) -> str:    return _color("2", s)
def _cyan(s: str) -> str:   return _color("36", s)
def _yellow(s: str) -> str: return _color("33", s)


def _pick_category(
    merchant: dict,
    ynab_categories: list,
    category_groups: list,
    ynab_client: YNABClient,
    budget_id: str,
) -> Optional[str]:
    """Show the per-merchant category picker. Returns the chosen YNAB
    category id, or None if the user backed out."""
    cats_used: dict = merchant.get("categories_used", {}) or {}
    # Build {category_id: category_object} for quick lookups, excluding
    # hidden/deleted.
    by_id = {
        c.id: c
        for c in ynab_categories
        if not getattr(c, "hidden", False) and not getattr(c, "deleted", False)
    }
    # Sort used categories by frequency descending.
    used_sorted = sorted(
        [(cid, cnt) for cid, cnt in cats_used.items() if cid in by_id],
        key=lambda kv: (-kv[1], by_id[kv[0]].name.lower()),
    )

    while True:
        print()
        print(f"  {_bold('Categories for')} '{merchant.get('alias', '?')}':")
        for i, (cid, cnt) in enumerate(used_sorted, start=1):
            c = by_id[cid]
            print(f"   {i}. {c.name} {_dim(f'(used {cnt}×)')}")
        print()
        print(f"   {_dim('o)')} Other category")
        print(f"   {_dim('n)')} New category")
        print(f"   {_dim('b)')} Back")
        print()

        raw = input(_cyan("  Pick: ")).strip().lower()

        if not raw:
            continue
        if raw == "b":
            return None
        if raw == "o":
            picked = _pick_category_from_full_list(category_groups)
            if picked:
                return picked
            continue
        if raw == "n":
            picked = _create_new_category(category_groups, ynab_client, budget_id)
            if picked:
                return picked
            continue
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(used_sorted):
                return used_sorted[n - 1][0]
            print(f"  Out of range (1..{len(used_sorted)})")
            continue
        print(f"  Unrecognized: {raw!r}")


def _pick_category_from_full_list(category_groups: list) -> Optional[str]:
    """Stub: filled in Task 7b. Returns None for now."""
    print(_dim("  (Other-category picker not implemented yet)"))
    return None


def _create_new_category(
    category_groups: list, ynab_client: YNABClient, budget_id: str
) -> Optional[str]:
    """Stub: filled in Task 8. Returns None for now."""
    print(_dim("  (New-category flow not implemented yet)"))
    return None
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): single-category picker

Lists merchant.categories_used sorted by frequency descending, with
options for Other / New / Back. Other and New are stubbed for now."
```

---

## Task 7b: Full-list picker (`_pick_category_from_full_list`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _pick_category_from_full_list


class TestPickCategoryFromFullList(unittest.TestCase):
    def _cat(self, cid, name, hidden=False, deleted=False):
        c = MagicMock()
        c.id = cid; c.name = name
        c.hidden = hidden; c.deleted = deleted
        return c

    def _grp(self, gid, name, cats):
        g = MagicMock()
        g.id = gid; g.name = name; g.categories = cats
        return g

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="2")
    def test_picks_by_global_number(self, _input, _stdout):
        groups = [
            self._grp("g1", "Bills", [self._cat("c1", "Internet"), self._cat("c2", "Power")]),
            self._grp("g2", "Fun",   [self._cat("c3", "Movies")]),
        ]
        self.assertEqual(_pick_category_from_full_list(groups), "c2")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="")
    def test_empty_input_returns_none(self, _input, _stdout):
        self.assertIsNone(_pick_category_from_full_list([]))

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["", "1"])
    def test_skips_hidden_and_deleted(self, _input, _stdout):
        groups = [
            self._grp("g1", "X", [
                self._cat("c1", "Hidden", hidden=True),
                self._cat("c2", "Real"),
            ]),
        ]
        # Picks 1 -> the only non-hidden category
        self.assertEqual(_pick_category_from_full_list(groups), "c2")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestPickCategoryFromFullList -v`
Expected: FAIL (stub returns None unconditionally).

- [ ] **Step 3: Replace the stub**

In `transactions.py`, replace `_pick_category_from_full_list` with:

```python
def _pick_category_from_full_list(category_groups: list) -> Optional[str]:
    """Flat numbered picker over every active YNAB category, grouped by
    category group for readability. Returns the chosen category id, or
    None if the user backs out (empty input)."""
    # Flatten while preserving group order.
    flat = []
    for g in category_groups:
        for c in getattr(g, "categories", []) or []:
            if getattr(c, "hidden", False) or getattr(c, "deleted", False):
                continue
            flat.append((g, c))

    if not flat:
        print(_dim("  No categories available."))
        return None

    print()
    last_group_id = None
    for i, (g, c) in enumerate(flat, start=1):
        if g.id != last_group_id:
            print(f"  {_bold(g.name)}")
            last_group_id = g.id
        print(f"   {i:>3}. {c.name}")
    print()
    raw = input(_cyan("  Pick a number, Enter to go back: ")).strip()
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(flat):
            return flat[n - 1][1].id
        print(f"  Out of range (1..{len(flat)})")
    return None
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): full-list category picker (o option)

Numbered list of every active YNAB category, grouped by category
group. Empty input cancels."
```

---

## Task 8: New-category creation (`_create_new_category` + new group)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _create_new_category


class TestCreateNewCategory(unittest.TestCase):
    def _grp(self, gid, name):
        g = MagicMock()
        g.id = gid; g.name = name; g.categories = []
        return g

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["Pet Supplies", "1"])
    def test_creates_category_in_existing_group(self, _input, _stdout):
        groups = [self._grp("g1", "Bills"), self._grp("g2", "Fun")]
        client = MagicMock()
        new_cat = MagicMock(id="new-c", name="Pet Supplies", category_group_id="g1")
        client.create_category.return_value = new_cat

        result = _create_new_category(groups, client, "bid")

        self.assertEqual(result, "new-c")
        client.create_category.assert_called_once_with("bid", "Pet Supplies", "g1")
        client.create_category_group.assert_not_called()
        # Side effect: the group's categories should include the new one.
        self.assertIn(new_cat, groups[0].categories)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["Cleaning", "n", "Home Maintenance"])
    def test_creates_new_group_then_category(self, _input, _stdout):
        groups = []
        client = MagicMock()
        new_grp = MagicMock(id="new-g", name="Home Maintenance", categories=[])
        new_cat = MagicMock(id="new-c", name="Cleaning", category_group_id="new-g")
        client.create_category_group.return_value = new_grp
        client.create_category.return_value = new_cat

        result = _create_new_category(groups, client, "bid")

        self.assertEqual(result, "new-c")
        client.create_category_group.assert_called_once_with("bid", "Home Maintenance")
        client.create_category.assert_called_once_with("bid", "Cleaning", "new-g")
        # Both the new group and new category should be appended to the in-memory list.
        self.assertIn(new_grp, groups)
        self.assertIn(new_cat, new_grp.categories)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["", ""])
    def test_empty_name_returns_none(self, _input, _stdout):
        result = _create_new_category([], MagicMock(), "bid")
        self.assertIsNone(result)
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestCreateNewCategory -v`
Expected: FAIL (current stub returns None unconditionally).

- [ ] **Step 3: Replace the stub**

In `transactions.py`, replace `_create_new_category` with:

```python
def _create_new_category(
    category_groups: list, ynab_client: YNABClient, budget_id: str
) -> Optional[str]:
    """Walk the user through creating a new category (with the option to
    also create a new group on the fly). Returns the new category's id, or
    None if cancelled.

    Side effect: appends the new category to the chosen group's `.categories`
    list (and the new group to `category_groups` if one was created), so
    later prompts in the same run see them without re-fetching from YNAB.
    """
    name = input(_cyan("  New category name (Enter to cancel): ")).strip()
    if not name:
        return None

    # Pick or create a group
    print()
    print(_bold("  Target group:"))
    for i, g in enumerate(category_groups, start=1):
        print(f"   {i:>3}. {g.name}")
    print(f"   {_dim('n)')} New group")
    print(f"   {_dim('b)')} Back")
    print()

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
        elif raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(category_groups):
                chosen_group = category_groups[n - 1]
            else:
                print(f"  Out of range (1..{len(category_groups)})")
        else:
            print(f"  Unrecognized: {raw!r}")

    try:
        new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)
    except Exception as e:
        print(f"  Failed to create category: {e}")
        return None

    if chosen_group.categories is None:
        chosen_group.categories = []
    chosen_group.categories.append(new_cat)
    return new_cat.id
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): new-category creation (with new-group flow)

Creates the category in YNAB via create_category. If user picks 'n'
for the target group, creates the group first via create_category_group
then creates the category in it. Updates the in-memory groups so the
new entries are visible to subsequent prompts in the same run."
```

---

## Task 9: Memo prompts (`_prompt_memo`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _prompt_memo


class TestPromptMemo(unittest.TestCase):
    @patch("builtins.input", return_value="")
    def test_empty_uses_default(self, _input):
        self.assertEqual(_prompt_memo("default note"), "default note")

    @patch("builtins.input", return_value="")
    def test_empty_default_returns_empty(self, _input):
        self.assertEqual(_prompt_memo(""), "")

    @patch("builtins.input", return_value="custom note")
    def test_typed_value_replaces_default(self, _input):
        self.assertEqual(_prompt_memo("default note"), "custom note")

    @patch("builtins.input", return_value="  spaced  ")
    def test_strips_whitespace(self, _input):
        self.assertEqual(_prompt_memo(""), "spaced")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestPromptMemo -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Add the helper**

Add to `transactions.py`:

```python
def _prompt_memo(default: str = "") -> str:
    """Prompt for a memo. Press Enter to keep `default`. Strips whitespace."""
    if default:
        shown = f"  Memo (Enter to keep '{default}'): "
    else:
        shown = "  Memo (Enter for none): "
    raw = input(shown).strip()
    return raw if raw else default
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): _prompt_memo helper

Press Enter to keep the supplied default. Used for both parent and
per-split memos."
```

---

## Task 10: Split flow (`_collect_splits`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _collect_splits


class TestCollectSplits(unittest.TestCase):
    def _txn(self, amount):
        t = MagicMock()
        t.amount = amount
        return t

    def _category(self, cid, name):
        c = MagicMock()
        c.id = cid; c.name = name; c.hidden = False; c.deleted = False
        return c

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[
        # split count
        "2",
        # split 1 amount, category number, memo
        "60", "1", "fuel only",
        # split 2 amount (default = remaining), category number, memo
        "", "2", "",
    ])
    def test_collects_two_splits_with_remaining_default(self, _input, _stdout):
        txn = self._txn(-100000)  # -100.00
        merchant = {"alias": "Spar", "categories_used": {"c-petrol": 5, "c-snacks": 1}}
        categories = [self._category("c-petrol", "Petrol"), self._category("c-snacks", "Snacks")]
        splits = _collect_splits(txn, merchant, categories, [], MagicMock(), "bid")
        self.assertIsNotNone(splits)
        self.assertEqual(len(splits), 2)
        self.assertEqual(splits[0]["category_id"], "c-petrol")
        self.assertEqual(splits[0]["amount_milliunits"], -60000)
        self.assertEqual(splits[0]["memo"], "fuel only")
        self.assertEqual(splits[1]["category_id"], "c-snacks")
        self.assertEqual(splits[1]["amount_milliunits"], -40000)
        self.assertEqual(splits[1]["memo"], "")
        # All splits sum to txn.amount
        self.assertEqual(sum(s["amount_milliunits"] for s in splits), txn.amount)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["0"])
    def test_invalid_count_returns_none(self, _input, _stdout):
        txn = self._txn(-1000)
        merchant = {"alias": "X", "categories_used": {}}
        result = _collect_splits(txn, merchant, [], [], MagicMock(), "bid")
        self.assertIsNone(result)
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestCollectSplits -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement the split flow**

Add to `transactions.py`:

```python
def _collect_splits(
    txn,
    merchant: dict,
    ynab_categories: list,
    category_groups: list,
    ynab_client: YNABClient,
    budget_id: str,
) -> Optional[list]:
    """Walk the user through splitting `txn` across multiple categories.
    Returns a list of {category_id, amount_milliunits, memo} dicts summing
    exactly to txn.amount, or None if cancelled.

    Amounts use the same sign as txn.amount (typically negative for outflows).
    Each split's default amount is `remaining / splits_left`; the final
    split defaults to the exact remainder so the total reconciles.
    """
    total = txn.amount
    sign = -1 if total < 0 else 1
    abs_total = abs(total)

    raw = input(_cyan("  How many splits? [2]: ")).strip()
    n = 2
    if raw:
        try:
            n = int(raw)
        except ValueError:
            print("  Invalid count.")
            return None
    if n < 2:
        print("  Splits must be 2 or more.")
        return None

    splits = []
    remaining = abs_total
    for i in range(1, n + 1):
        splits_left = n - i + 1
        default_amt = (remaining / splits_left) if splits_left else 0
        # Show default with two decimals
        label = "remaining" if splits_left == 1 else f"{default_amt:.2f}"
        amt_raw = input(_cyan(f"  Split {i} of {n} — amount [{label}]: ")).strip()
        if amt_raw:
            try:
                amt_abs = float(amt_raw)
            except ValueError:
                print("  Invalid amount.")
                return None
            if amt_abs <= 0:
                print("  Amount must be positive.")
                return None
            amt_milli = int(round(amt_abs * 1000))
            if amt_milli > remaining:
                print(f"  Exceeds remaining {remaining/1000:.2f}.")
                return None
        else:
            # Default: divide remaining by splits_left, or take remainder on the last split.
            if splits_left == 1:
                amt_milli = remaining
            else:
                amt_milli = int(round(default_amt * 1000))

        cat_id = _pick_category(merchant, ynab_categories, category_groups, ynab_client, budget_id)
        if cat_id is None:
            return None

        memo = _prompt_memo("")

        splits.append({
            "category_id": cat_id,
            "amount_milliunits": sign * amt_milli,
            "memo": memo,
        })
        remaining -= amt_milli

    # If remainder didn't quite zero out (rounding), put it on the last split.
    if remaining != 0 and splits:
        splits[-1]["amount_milliunits"] += sign * remaining

    return splits
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): _collect_splits drives the split flow

Asks for split count upfront, then iterates: default amount per split
is remaining/splits_left (final split defaults to the exact remainder
so totals always reconcile). Each split picks its own category and
optional memo. Returns None to cancel."
```

---

## Task 11: Merchant memory update (`_update_merchant_memory`)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _update_merchant_memory


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

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestUpdateMerchantMemory -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Add to `transactions.py`:

```python
def _update_merchant_memory(store: ConfigStore, merchant: dict, txn) -> None:
    """Update the merchant's categories_used (frequency map) and
    last_processing (split structure for Enter-repeat) based on the just-
    categorized transaction. Persists via store.set_merchant_memory."""
    subs = list(getattr(txn, "subtransactions", []) or [])
    if subs:
        splits = [
            {
                "category_id": s["category_id"],
                "amount_milliunits": s["amount"],
                "memo": s.get("memo", "") or "",
            }
            for s in subs
        ]
    else:
        splits = [
            {
                "category_id": txn.category_id,
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

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): _update_merchant_memory

Increments categories_used counts (frequency-ranked picker) and
overwrites last_processing with the just-applied split structure
(used by Enter-repeat detection in the next prompt for this merchant)."
```

---

## Task 12: Enter-repeat detection and replay

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _can_repeat, _apply_repeat


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

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestRepeatHelpers -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `transactions.py`:

```python
def _can_repeat(merchant: dict, txn) -> bool:
    """True iff the merchant has a last_processing whose amount equals
    txn.amount exactly. (Enter-repeat fires only on exact-amount match.)"""
    lp = merchant.get("last_processing") if merchant else None
    if not lp:
        return False
    return lp.get("amount_milliunits") == getattr(txn, "amount", None)


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

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): Enter-repeat detection and replay

_can_repeat fires only when last_processing.amount_milliunits matches
txn.amount exactly. _apply_repeat clones the split structure (or
single category) onto the new transaction. Memos use fresh defaults
because they describe the specific transaction, not the merchant
pattern."
```

---

## Task 13: `_process_one_transaction` (top-level per-txn handler)

**Files:**
- Modify: `src/finab/transactions.py`
- Modify: `tests/test_transactions.py`

- [ ] **Step 1: Append failing tests**

Add to `tests/test_transactions.py`:

```python
from finab.transactions import _process_one_transaction


class TestProcessOneTransaction(unittest.TestCase):
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
        self.ynab_client = MagicMock()
        self.category = MagicMock(id="c-petrol", name="Petrol", hidden=False, deleted=False)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _txn(self, amount, merchant_id="fw-spar"):
        t = MagicMock()
        t.amount = amount
        t.merchant_id = merchant_id
        t.memo = "spar receipt"
        t.payee_id = None
        t.payee_name = None
        t.category_id = None
        t.subtransactions = []
        return t

    def test_inflow_auto_categorizes(self):
        txn = self._txn(50000)  # positive
        inflow_cat = MagicMock(id="c-inflow", name="Inflow: Ready to Assign",
                                hidden=False, deleted=False)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [inflow_cat], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-inflow")

    def test_no_merchant_returns_categorized_uncategorized(self):
        txn = self._txn(-1000, merchant_id=None)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertIsNone(txn.category_id)

    def test_transfer_merchant_sets_payee_no_category(self):
        # Create a transfer-payee merchant
        self.store.add_merchant(
            alias="Discovery Bank ZAR",
            fw_record={"id": "fw-xfer", "name": "Discovery"},
            ynab_record={"id": "yn-xfer-payee", "transfer_account_id": "yn-acc"},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-2000, merchant_id="fw-xfer")
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.payee_id, "yn-xfer-payee")
        self.assertIsNone(txn.category_id)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["c", "1", ""])
    def test_interactive_single_category(self, _input, _stdout):
        # First-time merchant: no categories_used yet, so user picks 'c' then
        # 'o' (other) is needed. Adapt by seeding categories_used.
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
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)

        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-petrol")
        # Payee resolved from merchant
        self.assertEqual(txn.payee_id, "yn-spar")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="f")
    def test_user_picks_flush(self, _input, _stdout):
        # Seed memory so an Enter-repeat would otherwise be offered.
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            last_processing={"amount_milliunits": -1000, "parent_memo": "",
                             "splits": [{"category_id": "c-petrol",
                                         "amount_milliunits": -1000, "memo": ""}]},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 1, 3, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "flush")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", return_value="")
    def test_enter_replays_last_processing_when_amount_matches(self, _input, _stdout):
        self.store.set_merchant_memory(
            self.merchant["id"],
            categories_used={"c-petrol": 1},
            last_processing={"amount_milliunits": -3000, "parent_memo": "",
                             "splits": [{"category_id": "c-petrol",
                                         "amount_milliunits": -3000, "memo": ""}]},
        )
        self.store = ConfigStore(self.path)
        txn = self._txn(-3000)
        outcome = _process_one_transaction(
            txn, 1, 1, 0, self.store, self.ynab_client, "bid",
            [self.category], [],
        )
        self.assertEqual(outcome, "categorized")
        self.assertEqual(txn.category_id, "c-petrol")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_transactions.py::TestProcessOneTransaction -v`
Expected: FAIL — `ImportError`.

- [ ] **Step 3: Implement**

Add to `transactions.py`:

```python
def _process_one_transaction(
    txn,
    idx: int,
    total: int,
    unflushed_count: int,
    store: ConfigStore,
    ynab_client: YNABClient,
    budget_id: str,
    ynab_categories: list,
    category_groups: list,
) -> str:
    """Drive the prompt loop for a single transaction.

    Returns one of:
      "categorized" — the transaction has been fully populated (caller
                      should enqueue it).
      "flush"       — user requested an immediate flush; caller should
                      flush the queue and then re-call this function for
                      the same transaction.
    """
    # --- (a) Positive amount: auto-inflow ---
    if _is_inflow(txn):
        inflow_id = _find_inflow_category(ynab_categories)
        if inflow_id:
            txn.category_id = inflow_id
            txn.subtransactions = []
            return "categorized"
        # If we couldn't find an inflow category, fall through and let the
        # user pick one manually like any other transaction.

    # --- (b) Resolve merchant ---
    merchant = None
    fw_mid = getattr(txn, "merchant_id", None)
    if fw_mid:
        merchant = store.merchant_by_finwise_id(fw_mid)

    # --- (c) Transfer: set payee, no category ---
    if _is_transfer(merchant):
        txn.payee_id = merchant["ynab"]["id"]
        txn.payee_name = None
        txn.category_id = None
        txn.subtransactions = []
        return "categorized"

    # --- (d) No merchant: push uncategorized ---
    if not merchant:
        txn.category_id = None
        txn.subtransactions = []
        return "categorized"

    # --- (e) Set payee from merchant ---
    txn.payee_id = merchant["ynab"].get("id")
    txn.payee_name = None

    # --- (f) Interactive header + prompt ---
    header = f" Transaction {idx} of {total} "
    if unflushed_count:
        header += f" ({unflushed_count} unflushed) "
    bar = "━" * max(0, 60 - len(header))
    print(f"\n{_cyan('━━━')}{_bold(_cyan(header))}{_cyan(bar)}")
    print(f"  {_dim('Merchant:')}  {merchant.get('alias', '?')}")
    print(f"  {_dim('Date:')}      {getattr(txn, 'date', '?')}")
    amount_str = f"{txn.amount / 1000:.2f}"
    print(f"  {_dim('Amount:')}    {amount_str}")
    print(f"  {_dim('Memo:')}      {getattr(txn, 'memo', '') or _dim('(none)')}")

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

    print(f"  Or:")
    print(f"    {_dim('s)')} Split into multiple categories")
    print(f"    {_dim('c)')} Pick a category")
    if unflushed_count:
        print(f"    {_dim('f)')} Flush {unflushed_count} pending to YNAB")
    print()

    while True:
        raw = input(_cyan("  > ")).strip().lower()
        if raw == "" and repeat_available:
            _apply_repeat(merchant, txn)
            return "categorized"
        if raw == "f" and unflushed_count:
            return "flush"
        if raw == "c":
            cat_id = _pick_category(merchant, ynab_categories, category_groups, ynab_client, budget_id)
            if cat_id is None:
                # User backed out of the picker; re-show prompt.
                continue
            txn.category_id = cat_id
            txn.subtransactions = []
            txn.memo = _prompt_memo(getattr(txn, "memo", "") or "")
            return "categorized"
        if raw == "s":
            subs = _collect_splits(txn, merchant, ynab_categories, category_groups, ynab_client, budget_id)
            if subs is None:
                continue
            txn.subtransactions = [
                {
                    "category_id": s["category_id"],
                    "amount": s["amount_milliunits"],
                    "memo": s["memo"],
                }
                for s in subs
            ]
            txn.category_id = None
            txn.memo = _prompt_memo(getattr(txn, "memo", "") or "")
            return "categorized"
        print(f"  Unrecognized: {raw!r}")


def _category_name(categories, category_id: str) -> Optional[str]:
    for c in categories:
        if c.id == category_id:
            return c.name
    return None
```

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/test_transactions.py -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_transactions.py
git commit -m "feat(transactions): _process_one_transaction top-level handler

Drives the per-transaction prompt loop. Handles inflow / transfer /
no-merchant auto-paths up-front, then renders the interactive header
+ prompt and dispatches to category picker, split flow, or Enter-
repeat. Returns 'categorized' or 'flush' for the caller to handle."
```

---

## Task 14: `sync_transactions` orchestrator

**Files:**
- Modify: `src/finab/transactions.py`
- Replace: `tests/test_sync_transactions.py`

- [ ] **Step 1: Replace `tests/test_sync_transactions.py` with the integration test**

Overwrite the file completely:

```python
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore
from finab.transactions import sync_transactions


class TestSyncTransactionsIntegration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        # Seed accounts and merchants for a complete run.
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-acc"},
            ynab_record={"id": "yn-acc", "transfer_payee_id": "tp-1"},
        )
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, import_id, account_id, amount, merchant_id, memo=""):
        from datetime import date
        t = MagicMock()
        t.import_id = import_id
        t.account_id = account_id
        t.amount = amount
        t.merchant_id = merchant_id
        t.date = date(2026, 5, 20)
        t.memo = memo
        t.subtransactions = []
        t.payee_id = None
        t.payee_name = None
        t.category_id = None
        t.ynab_id = None
        return t

    def _category(self, cid, name, hidden=False, deleted=False):
        c = MagicMock()
        c.id = cid; c.name = name
        c.hidden = hidden; c.deleted = deleted
        return c

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=["c", "1", ""])
    def test_full_flow_pushes_at_end(self, _input, _stdout):
        # One outflow transaction
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-tx-1", "fw-acc", -5000, "fw-spar", memo="purchase"),
        ]
        self.ynab_client.get_transactions.return_value = []
        # One eligible category, also used to seed picker
        cat = self._category("c-groceries", "Groceries")
        self.ynab_client.get_categories.return_value = [cat]
        self.ynab_client.get_category_groups_with_categories.return_value = []
        # Seed merchant memory so the picker shows the used category
        m = self.store.merchant_by_finwise_id("fw-spar")
        self.store.set_merchant_memory(
            m["id"],
            categories_used={"c-groceries": 1},
            last_processing={"amount_milliunits": -9999, "parent_memo": "",
                             "splits": [{"category_id": "c-groceries",
                                         "amount_milliunits": -9999, "memo": ""}]},
        )
        self.store = ConfigStore(self.path)

        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

        # Auto-flush at end should have pushed.
        self.ynab_client.create_transactions.assert_called_once()
        # Memory got updated.
        m2 = self.store.merchant_by_finwise_id("fw-spar")
        self.assertEqual(m2["categories_used"]["c-groceries"], 2)

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[])
    def test_positive_amount_auto_inflows(self, _input, _stdout):
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-tx-1", "fw-acc", 10000, "fw-spar"),
        ]
        self.ynab_client.get_transactions.return_value = []
        inflow = self._category("c-inflow", "Inflow: Ready to Assign")
        self.ynab_client.get_categories.return_value = [inflow]
        self.ynab_client.get_category_groups_with_categories.return_value = []

        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_transactions.assert_called_once()
        args = self.ynab_client.create_transactions.call_args
        pushed = args.args[1]
        self.assertEqual(pushed[0].category_id, "c-inflow")

    @patch("sys.stdout", new_callable=__import__("io").StringIO)
    @patch("builtins.input", side_effect=[])
    def test_no_transactions_to_process(self, _input, _stdout):
        self.fw_client.get_transactions.return_value = []
        self.ynab_client.get_transactions.return_value = []
        self.ynab_client.get_categories.return_value = []
        self.ynab_client.get_category_groups_with_categories.return_value = []

        # Should not raise; should not push anything.
        sync_transactions(self.fw_client, self.ynab_client, "bid", self.store)
        self.ynab_client.create_transactions.assert_not_called()
        self.ynab_client.update_transactions.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_sync_transactions.py -v`
Expected: FAIL — `sync_transactions` is still a stub (`NotImplementedError`).

- [ ] **Step 3: Replace the stub in `transactions.py`**

Replace `sync_transactions` with the real implementation:

```python
def _sort_key(store: ConfigStore):
    """Sort candidates by (merchant_alias, date_asc). Unknown-merchant
    transactions sort to the end."""
    def key(txn):
        mid = getattr(txn, "merchant_id", None)
        merchant = store.merchant_by_finwise_id(mid) if mid else None
        alias = merchant["alias"].lower() if merchant else "￿"
        d = getattr(txn, "date", None)
        return (alias, d if d is not None else date.max)
    return key


def _confirm(prompt: str) -> bool:
    """Yes/no prompt. Default Yes (empty -> True)."""
    raw = input(prompt).strip().lower()
    return raw in ("", "y", "yes")


def sync_transactions(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
) -> None:
    """Phase 3 orchestrator. Fetches all transactions, dedupes, and walks
    the user through categorizing each non-auto-handled transaction.
    Pushes via the _PendingQueue (manual 'f' flush, auto-flush at end,
    Ctrl+C asks before flushing)."""
    print("\n--- Transaction Sync ---")

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

    candidates = merge_and_filter_transactions(fw_txns, ynab_txns, store)
    candidates.sort(key=_sort_key(store))
    total = len(candidates)
    print(f"Transactions to process: {_yellow(str(total))}")

    queue = _PendingQueue()
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
                continue   # re-process the same transaction
            if outcome == "categorized":
                queue.add(txn)
                merchant = store.merchant_by_finwise_id(getattr(txn, "merchant_id", None))
                if merchant:
                    _update_merchant_memory(store, merchant, txn)
            idx += 1
    except KeyboardInterrupt:
        if queue.count() > 0:
            if _confirm(f"\nFlush {queue.count()} pending transactions before exit? [Y/n]: "):
                queue.flush(ynab_client, budget_id)
        raise
    finally:
        if queue.count() > 0:
            queue.flush(ynab_client, budget_id)
```

- [ ] **Step 4: Verify**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/finab/transactions.py tests/test_sync_transactions.py
git commit -m "feat(transactions): wire sync_transactions orchestrator

Fetches all transactions (no date filter), dedupes via the rewritten
merge_and_filter_transactions, sorts by (merchant_alias, date_asc),
drives _process_one_transaction in a loop, enqueues categorized
transactions via _PendingQueue, and flushes on demand / at end /
on Ctrl+C confirmation."
```

---

## Task 15: Wire `sync_transactions` into `main()` and delete the old pipeline

**Files:**
- Modify: `src/finab/main.py`

- [ ] **Step 1: Update the import in `main.py`**

At the top of `main.py`, add:

```python
from finab.transactions import sync_transactions as _new_sync_transactions
```

Find the old `sync_transactions` function (around line 1579 — the one with signature `sync_transactions(finwise_client, ynab_client, budget_id, store)`). Don't delete it yet; just stop calling it.

In `main()`, find the line that calls the old `sync_transactions`:

```python
            sync_transactions(fw_client, ynab_client, budget_id, store)
```

Replace with:

```python
            _new_sync_transactions(fw_client, ynab_client, budget_id, store)
```

- [ ] **Step 2: Verify everything still imports and tests pass**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 3: Delete the legacy pipeline functions from `main.py`**

Delete these top-level function definitions (use `grep -n "^def " src/finab/main.py` to find line numbers):

- `normalize_payee_for_matching` (~line 45)
- `map_accounts` (~line 640)
- `fetch_transactions` (~line 684)
- `process_payee_aliases` (~line 711, ~210 lines)
- `merge_and_filter_transactions` (~line 920) — the new version is in `transactions.py`
- `_build_cache`, `_apply_cache` (~lines 1037, 1057)
- `collect_split_subtransactions` (~line 1083)
- `process_categories` (~line 1150, ~360 lines)
- `sync_changes_to_ynab` (~line 1515)
- `sync_transactions` (the old one, ~line 1579)

Also clean up imports at the top of `main.py` that are now unused. After deletion:

```python
from finab.config import (
    load_budget_id,
    save_budget_id,
)
```

(Remove `load_payee_rules`, `save_payee_rules`, `load_merchant_aliases`, `load_category_rules`, `save_category_rules`, `load_aliases`, `load_import_id_offset`, `load_cache`, `save_cache`, `clear_cache` from the import block if they're listed.)

Other imports that may have become unused (verify via grep before deleting):

- `import re` (only used in regex rule loops — now gone)
- `import random`, `import string` (already cleaned in earlier work)
- `import hashlib` (used by `generate_import_id` — keep)
- The `YNABTransaction, Transaction` import — verify whether still used after deletions.

Run `uv run python -c "import finab.main"` after each round of deletions to catch import errors early.

- [ ] **Step 4: Rename the new sync import**

Now that the old `sync_transactions` is gone, simplify the import:

In `main.py`:

```python
from finab.transactions import sync_transactions as _new_sync_transactions
```

becomes:

```python
from finab.transactions import sync_transactions
```

And the call in `main()`:

```python
            _new_sync_transactions(fw_client, ynab_client, budget_id, store)
```

becomes:

```python
            sync_transactions(fw_client, ynab_client, budget_id, store)
```

- [ ] **Step 5: Verify**

Run: `uv run pytest -q`
Expected: All tests pass.

Run: `wc -l src/finab/main.py`
Expected: main.py is now around 900 lines (down from 1714).

- [ ] **Step 6: Commit**

```bash
git add src/finab/main.py
git commit -m "refactor(main): delete legacy transaction pipeline, wire new Phase 3

Removed:
- normalize_payee_for_matching, map_accounts, fetch_transactions
- process_payee_aliases (~210 lines): merchant resolution moved to Phase 2
- merge_and_filter_transactions: rewritten in transactions.py
- _build_cache, _apply_cache: no cache file in the new design
- collect_split_subtransactions: replaced by _collect_splits
- process_categories (~360 lines): replaced by _process_one_transaction
- sync_changes_to_ynab: replaced by _PendingQueue.flush
- sync_transactions (old): replaced by transactions.sync_transactions

main() now imports sync_transactions from finab.transactions.
main.py shrinks from 1714 to ~900 lines."
```

---

## Task 16: Delete dead helpers from `config.py`

**Files:**
- Modify: `src/finab/config.py`

- [ ] **Step 1: Delete the regex-rule and cache helpers**

In `src/finab/config.py`, delete:

- `load_payee_rules(...)` and `save_payee_rules(...)` (~10 lines)
- `load_category_rules(...)` and `save_category_rules(...)` (~10 lines)
- `load_cache(...)`, `save_cache(...)`, `clear_cache(...)` (~25 lines)
- The `CACHE_FILE` constant near the top (~2 lines)

Keep:
- `load_budget_id`, `save_budget_id`
- `load_import_id_offset`, `save_import_id_offset`
- `load_aliases`, `load_merchant_aliases` (the compat shims, still used by the old map_accounts during transition... wait, map_accounts was deleted in Task 15. Verify these shims have no remaining callers.)

Run `grep -rn "load_aliases\|load_merchant_aliases" src/ tests/` — if the only matches are the definitions themselves and the test file `tests/test_config_shims.py`, these shims are also dead and can be deleted along with `tests/test_config_shims.py`.

- [ ] **Step 2: Run tests, verify**

Run: `uv run pytest -q`
Expected: All tests pass.

- [ ] **Step 3: Check the test suite for stale tests**

Run: `grep -rn "load_payee_rules\|save_payee_rules\|load_category_rules\|save_category_rules\|load_cache\|save_cache\|clear_cache" tests/`

If any test references these deleted symbols, the test file is testing dead behavior — delete or rewrite. Most likely candidates: nothing in the current test suite uses them directly. Verify before proceeding.

- [ ] **Step 4: Commit**

```bash
git add src/finab/config.py tests/
git commit -m "refactor(config): delete dead regex-rule and cache helpers

The new Phase 3 design doesn't use regex categorization rules or the
cache.json abort-resume file. Removed:
- load_payee_rules / save_payee_rules
- load_category_rules / save_category_rules
- load_cache / save_cache / clear_cache
- CACHE_FILE constant

If load_aliases / load_merchant_aliases shims have no remaining
callers after Task 15's deletions, those go too (along with their
test file)."
```

---

## Task 17: Manual verification

**Files:** None (manual run against real APIs).

This task does not produce a commit. Run it after Task 16 lands.

- [ ] **Step 1: Back up `config.json`**

```bash
cp config.json config.json.phase3.bak
```

- [ ] **Step 2: Inspect store entries**

```bash
jq '.accounts | length, .merchants | length' config.json
```

Expected: both are populated from prior Phase 1 / Phase 2 runs.

- [ ] **Step 3: Run the app**

```bash
uv run finab
```

You should see:
- `--- Account Sync ---` (Phase 1) — no prompts unless a new FinWise account has appeared.
- `--- Merchant Sync ---` (Phase 2) — no prompts unless a new FinWise merchant has appeared.
- `--- Transaction Sync ---` (Phase 3, **new**) — header showing the number of transactions to process.

- [ ] **Step 4: Walk through Phase 3**

For each prompted transaction, verify:

1. Header shows `Transaction N of M (X unflushed)`, the merchant alias, date, amount, and memo.
2. If amount matches the merchant's last_processing exactly, the `[Enter] to repeat last:` line shows the previous decision.
3. Picking `c` lists the merchant's used categories sorted by frequency descending.
4. Picking `s` walks through the split flow (split count upfront, default amount per split, last default is remainder, per-split category + memo, then parent memo).
5. Picking `f` triggers a flush — verify the transaction shows up in YNAB before continuing the same transaction's prompt.
6. After a positive-amount transaction, no prompt: auto-categorized as "Inflow: Ready to Assign" and queued.
7. After a transfer-merchant transaction, no prompt: queued with `payee_id` set to the transfer payee and no `category_id`.

- [ ] **Step 5: Verify final state**

After the run finishes (auto-flush at end), check:

- The latest transactions are present in YNAB with correct payee, category, and amount.
- `config.json`: `merchants.<id>.categories_used` reflects the new counts; `last_processing` reflects the most-recent decision.

- [ ] **Step 6: Test Ctrl+C path**

Run `uv run finab` again. After Phase 3 prompts a couple of times, press Ctrl+C. You should see:

```
Flush N pending transactions before exit? [Y/n]:
```

Type `y` (or Enter): the pending transactions push to YNAB before exit.

Run `uv run finab` one more time. The flushed transactions should be filtered out (dedup via `import_id`); only the un-categorized remainder appears.

- [ ] **Step 7: Verify new-category path**

In a Phase 3 prompt, pick `c` then `n`. Walk through creating a new category. After the run finishes, verify the new category exists in YNAB's UI.

If you want to also test new-group creation: pick `c` → `n` → for "Target group" pick `n`. Verify both the new group and the new category appear in YNAB.

- [ ] **Step 8: Resolve any open implementation questions surfaced**

If pagination on YNAB's `get_transactions` ever returns truncated data (very large history), surface that as a follow-up. Otherwise the spec's "Open Implementation Questions" can be considered resolved.

---

## Summary of Commits

After all tasks, the branch should have 16 atomic commits (Task 17 is verification only):

1. `feat(store): add set_merchant_memory for Phase 3 categorization`
2. `feat(ynab): add create_category and create_category_group`
3. `feat(ynab): add get_category_groups_with_categories`
4. `feat(transactions): scaffold module with _PendingQueue`
5. `feat(transactions): auto-path detection helpers`
6. `feat(transactions): merge_and_filter rewritten against ConfigStore`
7. `feat(transactions): single-category picker`
7b. `feat(transactions): full-list category picker (o option)`
8. `feat(transactions): new-category creation (with new-group flow)`
9. `feat(transactions): _prompt_memo helper`
10. `feat(transactions): _collect_splits drives the split flow`
11. `feat(transactions): _update_merchant_memory`
12. `feat(transactions): Enter-repeat detection and replay`
13. `feat(transactions): _process_one_transaction top-level handler`
14. `feat(transactions): wire sync_transactions orchestrator`
15. `refactor(main): delete legacy transaction pipeline, wire new Phase 3`
16. `refactor(config): delete dead regex-rule and cache helpers`

The ordering is safe to bisect: every commit leaves the test suite green and the app functional. Tasks 15–16 are the only ones that delete more than they add; the test suite catches any latent reference to a deleted symbol.
