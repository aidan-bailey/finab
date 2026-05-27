# Textual TUI — Plan 1: Engine Extraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract the pure (non-interactive, non-Textual) logic from `transactions.py` and `main.py` into a new `engine/` subpackage. Build `SyncEngine` as a state-machine wrapper around phase 3. Keep the existing CLI working unchanged via re-exports. No UI changes in this plan.

**Architecture:** New `src/finab/engine/` subpackage with three modules: `sync.py` (SyncEngine + the pure helpers it depends on), `accounts.py` (pure phase 1 helpers), `merchants.py` (pure phase 2 helpers). `transactions.py` and `main.py` re-export the moved names so existing imports — including the test suite — keep working. The existing prompt-based CLI continues to function and uses the same helpers it always did, just imported via re-export.

**Tech Stack:** Python 3.13, uv (package manager), pytest, pydantic (existing `Transaction` / `Account` models), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-textual-tui-design.md` (migration plan steps 1–3).

**Scope boundary:** This plan does NOT add any Textual code, change the CLI entrypoint, or modify user-facing behaviour. Plans 2 and 3 (to be written after this lands) cover the TUI itself and the cutover.

---

## File Structure

**Created in this plan:**

- `src/finab/engine/__init__.py` — package marker, empty docstring only
- `src/finab/engine/sync.py` — moved pure helpers + `Candidate` dataclass + `SyncEngine` class
- `src/finab/engine/accounts.py` — pure helpers extracted from `main.py`'s phase 1 logic
- `src/finab/engine/merchants.py` — pure helpers extracted from `main.py`'s phase 2 logic
- `tests/engine/__init__.py` — package marker
- `tests/engine/test_sync_helpers.py` — tests for the moved-only helpers (smoke level — existing test_transactions.py covers them already via re-export)
- `tests/engine/test_sync_engine.py` — tests for the new `SyncEngine` class
- `tests/engine/test_accounts_helpers.py` — smoke tests confirming extracted helpers still pass
- `tests/engine/test_merchants_helpers.py` — smoke tests confirming extracted helpers still pass

**Modified in this plan:**

- `src/finab/transactions.py` — remove pure-function bodies, replace with `from finab.engine.sync import ...` re-export block at top. `TransactionsStore`, `TRANSACTIONS_FILE`, the interactive prompt helpers (`_pick_category`, `_collect_splits`, `_process_one_transaction`, etc.), `_PendingQueue`, `sync_transactions`, the ANSI colour helpers stay put.
- `src/finab/main.py` — remove pure-function bodies for `_link_account_transfer_payee`, `_extract_distinct_merchants`, `_calculate_starting_balance`, `_account_with_overrides`, `_reconcile_store_accounts_to_ynab`, `_reconcile_store_merchants_to_ynab`, replacing with re-exports from `engine/accounts.py` and `engine/merchants.py`. `sync_accounts`, `sync_merchants`, `main`, the interactive prompt helpers, the ANSI colour helpers stay put.

**Untouched:**

- `src/finab/client.py`, `ynab_client.py`, `models.py`, `config.py`, `store.py` — already pure / I/O-bounded as designed.
- All existing test files — they should keep passing without modification.

---

## Task 1: Scaffold engine package and tests directory

**Files:**
- Create: `src/finab/engine/__init__.py`
- Create: `tests/engine/__init__.py`

- [ ] **Step 1: Create the engine package init**

Write `src/finab/engine/__init__.py` with just a module docstring:

```python
"""Headless engine modules — no Textual imports, no interactive I/O.

`sync.py` owns phase 3 (transaction processing) as a state machine.
`accounts.py` and `merchants.py` hold pure helpers for phases 1 and 2.

Import direction: `tui/*` may import from here; this package may NOT
import anything from `finab.tui` or `textual`.
"""
```

- [ ] **Step 2: Create the tests/engine package init**

Write `tests/engine/__init__.py` as an empty file (pytest discovery only).

- [ ] **Step 3: Run the existing test suite to confirm nothing broke**

Run: `uv run pytest`
Expected: all existing tests pass; no new tests yet.

- [ ] **Step 4: Commit**

```bash
git add src/finab/engine/__init__.py tests/engine/__init__.py
git commit -m "feat(engine): scaffold engine package and tests dir"
```

---

## Task 2: Move pure phase-3 helpers to engine/sync.py

This task moves a fixed list of pure functions and constants out of `transactions.py` into `engine/sync.py`, then re-exports them from `transactions.py` so existing imports (including in `tests/test_transactions.py`) keep working. No behaviour change.

**Files:**
- Create: `src/finab/engine/sync.py`
- Modify: `src/finab/transactions.py` (delete moved bodies, add re-export block at top)

**Names moved (the full list — verify nothing is missed):**

- Constants: `_INFLOW_CATEGORY_NAMES`, `_TRACKING_ACCOUNT_TYPES`
- Pure helpers: `_account_is_tracking`, `_is_inflow`, `_is_before_current_month`, `_is_transfer`, `_find_inflow_category`, `_closest_processing`, `_apply_repeat`, `_apply_processing_to_txn`, `_update_merchant_memory`, `_category_name`, `_render_splits`, `_sort_key`
- The dedup function: `merge_and_filter_transactions`

**Names that STAY in `transactions.py`:**

- `TRANSACTIONS_FILE` constant (referenced by `tests/conftest.py`)
- `TransactionsStore` class
- ANSI helpers `_color`, `_bold`, `_dim`, `_green`, `_cyan`, `_yellow`
- Interactive helpers `_pick_category`, `_pick_category_from_full_list`, `_create_new_category`, `_prompt_memo`, `_collect_splits`, `_pick_from_processings`, `_confirm`
- `_PendingQueue` class
- `_process_one_transaction`, `sync_transactions`

- [ ] **Step 1: Write a smoke test in tests/engine/test_sync_helpers.py asserting the helpers import from the new location**

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_sync_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.engine.sync'`

- [ ] **Step 3: Create src/finab/engine/sync.py and copy the listed functions into it**

Create the file with this header, followed by the moved bodies (cut from `transactions.py`):

```python
"""Pure helpers for phase 3 transaction processing.

These functions and constants were extracted from finab.transactions
to give phase 3 a non-interactive surface. transactions.py re-exports
them so existing call sites keep working.

No I/O of any kind here beyond reading from the ConfigStore / TransactionsStore
arguments passed in — no print(), no input(), no network.
"""
from datetime import date
from typing import Optional


# Names YNAB might use for the inflow category. Checked in this order.
_INFLOW_CATEGORY_NAMES = (
    "inflow: ready to assign",
    "ready to assign",
    "inflow: to be budgeted",
    "to be budgeted",
)


# Account types that are off-budget in YNAB ("Tracking" accounts).
_TRACKING_ACCOUNT_TYPES = frozenset({
    "otherAsset", "otherLiability",
    "mortgage", "autoLoan", "studentLoan",
    "personalLoan", "medicalDebt", "otherDebt",
})


# --- The rest below is a verbatim move from transactions.py ---
# (paste the bodies of: _account_is_tracking, _is_inflow,
# _is_before_current_month, _is_transfer, _find_inflow_category,
# merge_and_filter_transactions, _closest_processing, _apply_repeat,
# _apply_processing_to_txn, _update_merchant_memory, _category_name,
# _render_splits, _sort_key — exactly as they appear in transactions.py
# at lines 110-733 minus the interactive prompt helpers.)
```

For each function, copy the body and its existing docstring verbatim. Note that `_apply_repeat` calls `_closest_processing` and `_apply_processing_to_txn`; `_apply_processing_to_txn` is self-contained; `_update_merchant_memory` takes a `ConfigStore` argument. All these dependencies are within the moved set or are stdlib — no circular imports possible.

Two known internal references inside `merge_and_filter_transactions` to fix:
- `_yellow(...)` and `_dim(...)` are called for diagnostic output. These are ANSI helpers that stay in `transactions.py`. Replace those two calls inside the moved function with no-op wrappers:

```python
def _yellow(s: str) -> str: return s
def _dim(s: str) -> str: return s
```

Add these two module-private no-ops to `engine/sync.py` (above `merge_and_filter_transactions`). The engine module is non-printing by contract; if `transactions.py`'s `sync_transactions` wants colour, it'll add its own diagnostic prints around the engine call. The existing diagnostic `print(...)` lines inside `merge_and_filter_transactions` stay in place — they use `_yellow`/`_dim` from this module which are now no-ops, so the output is still readable just without colour. (We accept colour loss in dedup diagnostics as the price of cutting the engine free of presentation; the volume of these prints is small and the user can still read them.)

- [ ] **Step 4: Edit src/finab/transactions.py — delete the moved bodies and add re-exports at top**

Delete the listed function and constant definitions from `transactions.py` (the lines previously holding them). Then, immediately under the existing imports at the top of the file, add this re-export block:

```python
# --- Re-exports from finab.engine.sync ---
# These functions and constants moved to finab.engine.sync; we re-export
# them here so existing import call sites (and the test suite) keep working.
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
```

Keep `TransactionsStore`, `TRANSACTIONS_FILE`, the ANSI helpers, the interactive helpers, `_PendingQueue`, `_process_one_transaction`, and `sync_transactions` in place — they stay in this module.

- [ ] **Step 5: Run the smoke tests**

Run: `uv run pytest tests/engine/test_sync_helpers.py -v`
Expected: PASS — both test functions.

- [ ] **Step 6: Run the entire existing test suite**

Run: `uv run pytest`
Expected: PASS — every test that was passing before continues to pass. Specifically, the rich behaviour tests in `tests/test_transactions.py` for `merge_and_filter_transactions`, `_closest_processing`, `_apply_processing_to_txn`, `_render_splits`, etc., should all still pass because their import path (`from finab.transactions import ...`) now goes through the re-export.

If any test fails: read the error, fix the missing/renamed/broken thing in `engine/sync.py` or the re-export block, and rerun.

- [ ] **Step 7: Commit**

```bash
git add src/finab/engine/sync.py src/finab/transactions.py tests/engine/test_sync_helpers.py
git commit -m "refactor(engine): move pure phase-3 helpers to engine/sync.py"
```

---

## Task 3: Define Candidate dataclass

`SyncEngine` operates on a list of `Candidate` objects — one per FW transaction that survives dedup. Each candidate wraps the `Transaction`, tracks its `status` in the per-run workflow, records whether an auto-rule fired, and stashes a snapshot of pre-decision state to support undo.

**Files:**
- Modify: `src/finab/engine/sync.py` (add `Candidate` at the bottom, after the moved helpers)
- Test: `tests/engine/test_sync_engine.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/engine/test_sync_engine.py`:

```python
"""Tests for finab.engine.sync.SyncEngine and Candidate.

These exercise the headless state machine — no Textual, no client calls.
SyncEngine.flush is tested separately with a stub client.
"""
from dataclasses import is_dataclass

import pytest

from finab.engine.sync import Candidate


class TestCandidate:
    def test_is_a_dataclass(self):
        assert is_dataclass(Candidate)

    def test_default_status_is_pending(self):
        c = Candidate(id="abc", txn=object())
        assert c.status == "pending"
        assert c.auto_reason is None
        assert c.prior_state is None

    def test_can_set_status_and_auto_reason(self):
        c = Candidate(id="abc", txn=object(), status="auto", auto_reason="inflow")
        assert c.status == "auto"
        assert c.auto_reason == "inflow"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_sync_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'Candidate' from 'finab.engine.sync'`.

- [ ] **Step 3: Implement Candidate in src/finab/engine/sync.py**

Append to the bottom of `engine/sync.py`:

```python
from dataclasses import dataclass, field
from typing import Any, Literal, Optional


CandidateStatus = Literal["pending", "auto", "decided", "flushed"]
"""
pending  — needs user input
auto     — engine auto-applied (inflow/transfer/no-merchant/pre-month)
decided  — user applied a category/split/transfer
flushed  — pushed to YNAB
"""


AutoReason = Literal["inflow", "transfer", "no-merchant", "pre-month"]


@dataclass
class Candidate:
    """One transaction in the per-run workflow.

    `txn` is the FinWise-side Transaction; `merge_and_filter_transactions`
    may have already mutated its `import_id` to our durable id, and may
    have set `ynab_id` if this is an UPDATE rather than a CREATE.
    """
    id: str                              # stable; we use txn.import_id (durable our_id)
    txn: Any                             # finab.models.Transaction — Any to avoid import cycle here
    status: CandidateStatus = "pending"
    auto_reason: Optional[AutoReason] = None
    # Snapshot of {category_id, subtransactions, payee_id, payee_name, memo}
    # taken at the moment a user decision is applied. Used by undo() to
    # restore the pre-decision state. None on pending or auto candidates.
    prior_state: Optional[dict] = None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/engine/test_sync_engine.py -v`
Expected: PASS — all three tests.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): add Candidate dataclass for phase-3 state machine"
```

---

## Task 4: SyncEngine constructor and auto-rule application

`SyncEngine.__init__` runs `merge_and_filter_transactions` to get the post-dedup list, sorts via `_sort_key`, and builds one `Candidate` per transaction. For each, it applies the auto-rules from `_process_one_transaction` (a/c/d/d2) and sets `status=auto` with the appropriate `auto_reason`. Transactions that don't match an auto-rule stay `status=pending`.

**Files:**
- Modify: `src/finab/engine/sync.py` (add `SyncEngine` class)
- Modify: `tests/engine/test_sync_engine.py` (add `TestSyncEngineLoad`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_sync_engine.py`:

```python
from pathlib import Path

from finab.engine.sync import SyncEngine
from finab.models import Transaction
from finab.store import ConfigStore
from finab.transactions import TransactionsStore


def _build_txn(
    *,
    fw_uuid: str,
    amount: int,
    merchant_id: str = None,
    account_id: str,
    date_str: str = "2026-05-22",
    memo: str = "test",
    is_transfer: bool = False,
):
    """Construct a Transaction matching what FinWiseClient produces."""
    from datetime import date as date_cls
    y, m, d = (int(x) for x in date_str.split("-"))
    return Transaction(
        import_id=fw_uuid,
        amount=amount,
        date=date_cls(y, m, d),
        memo=memo,
        merchant_id=merchant_id,
        account_id=account_id,
        is_transfer=is_transfer,
    )


def _seeded_store(tmp_path: Path) -> ConfigStore:
    """Return a ConfigStore with one mapped account and no merchants."""
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    return store


class TestSyncEngineLoad:
    def test_empty_inputs_produces_empty_candidates(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        engine = SyncEngine(
            fw_transactions=[],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert engine.candidates == []

    def test_inflow_sets_status_auto(self, tmp_path):
        """A positive-amount transaction should auto-resolve when an
        'Inflow: Ready to Assign' category exists."""
        # Stub category object — only `id`, `name`, `hidden`, `deleted` are read.
        class FakeCategory:
            def __init__(self, id, name):
                self.id = id; self.name = name; self.hidden = False; self.deleted = False
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-1", amount=12345, account_id="fw-acc-1")
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "inflow"
        assert str(c.txn.category_id) == "cat-rta"

    def test_no_merchant_sets_status_auto(self, tmp_path):
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-2", amount=-4200, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "no-merchant"

    def test_unknown_account_is_dropped_by_dedup(self, tmp_path):
        """merge_and_filter_transactions drops txns whose account isn't
        mapped — those should not appear as candidates at all."""
        store = ConfigStore(tmp_path / "config.json")  # no accounts mapped
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-3", amount=-1000, account_id="fw-unknown")
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert engine.candidates == []

    def test_pre_current_month_sets_status_auto(self, tmp_path):
        """A txn dated before the first of the current month, with a
        known merchant, should auto-resolve with reason='pre-month'."""
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="OldShop",
            fw_record={"id": "fw-merchant-1", "name": "OldShop", "samples": []},
            ynab_record={"id": "yn-pay-1", "name": "OldShop", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        # 2000-01-01 is unambiguously before 'today' in this codebase's lifetime
        txn = _build_txn(
            fw_uuid="fw-4", amount=-500,
            account_id="fw-acc-1", merchant_id="fw-merchant-1",
            date_str="2000-01-01",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "auto"
        assert c.auto_reason == "pre-month"

    def test_unresolved_txn_stays_pending(self, tmp_path):
        """Current-month txn with a known merchant that isn't a transfer
        payee — engine has no auto-rule for it, user must decide."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        assert len(engine.candidates) == 1
        c = engine.candidates[0]
        assert c.status == "pending"
        assert c.auto_reason is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestSyncEngineLoad -v`
Expected: FAIL — `ImportError: cannot import name 'SyncEngine'`.

- [ ] **Step 3: Implement SyncEngine.__init__ in src/finab/engine/sync.py**

Append to the bottom of `engine/sync.py`:

```python
class SyncEngine:
    """State machine for phase 3 (transaction sync).

    Construction does the work of `merge_and_filter_transactions` + the
    sort + the auto-rule pass from the original `_process_one_transaction`.
    The result is `self.candidates`, a list of Candidate objects in the
    order the UI should walk them.

    The engine never prints, never reads stdin, and never calls a network
    client during construction — flush() is the only method that calls
    YNAB.
    """

    def __init__(
        self,
        *,
        fw_transactions,
        ynab_transactions,
        ynab_categories,
        store,
        tx_store,
    ):
        self._store = store
        self._ynab_categories = ynab_categories

        # 1. Dedup and sort.
        merged = merge_and_filter_transactions(
            fw_transactions, ynab_transactions, store, tx_store
        )
        merged.sort(key=_sort_key(store))

        # 2. Build Candidate per txn and apply auto-rules.
        self.candidates: list[Candidate] = [
            self._build_candidate(txn) for txn in merged
        ]

    def _build_candidate(self, txn) -> Candidate:
        """Construct a Candidate around `txn` and apply auto-rules.

        Auto-rules, in priority order — same as _process_one_transaction:
          (a) inflow: positive amount + inflow category exists
          (b) transfer: txn's merchant links to an account's transfer payee
          (c) no-merchant: no merchant resolvable
          (d) pre-month: txn dated before first of current month, with merchant
        Otherwise: status = pending.
        """
        # `txn.import_id` is now our durable id (set by merge_and_filter_transactions).
        cid = txn.import_id
        candidate = Candidate(id=cid, txn=txn)

        # (a) Inflow
        if _is_inflow(txn):
            inflow_id = _find_inflow_category(self._ynab_categories)
            if inflow_id:
                txn.category_id = inflow_id
                txn.subtransactions = []
                candidate.status = "auto"
                candidate.auto_reason = "inflow"
                return candidate
            # No inflow category — fall through to merchant logic
            # (matches today's _process_one_transaction).

        merchant = None
        fw_mid = getattr(txn, "merchant_id", None)
        if fw_mid:
            merchant = self._store.merchant_by_finwise_id(fw_mid)

        # (b) Transfer
        if _is_transfer(merchant):
            txn.payee_id = merchant["ynab"]["id"]
            txn.payee_name = None
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "auto"
            candidate.auto_reason = "transfer"
            return candidate

        # (c) No merchant
        if not merchant:
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "auto"
            candidate.auto_reason = "no-merchant"
            return candidate

        # (d) Before current month
        if _is_before_current_month(txn):
            txn.payee_id = merchant["ynab"].get("id")
            txn.payee_name = None
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "auto"
            candidate.auto_reason = "pre-month"
            return candidate

        # Default: pending — user must decide. We still set the payee from
        # the merchant since that's not a decision the user makes.
        txn.payee_id = merchant["ynab"].get("id")
        txn.payee_name = None
        return candidate
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestSyncEngineLoad -v`
Expected: PASS — all five tests.

- [ ] **Step 5: Run the full test suite**

Run: `uv run pytest`
Expected: nothing previously passing now fails.

- [ ] **Step 6: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine constructor with auto-rule pass"
```

---

## Task 5: SyncEngine.apply_category

User picks a single category for a candidate. Engine: snapshots prior state, mutates the Transaction (`category_id`, `subtransactions = []`, optional memo), updates merchant memory if the candidate has a resolvable merchant, sets `status = decided`.

**Files:**
- Modify: `src/finab/engine/sync.py` (add `apply_category` method)
- Modify: `tests/engine/test_sync_engine.py` (add `TestApplyCategory`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_sync_engine.py`:

```python
class TestApplyCategory:
    def _setup(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        return engine, store

    def test_marks_decided_and_sets_category(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groceries", memo="produce")
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groceries"
        assert c.txn.subtransactions == []
        assert c.txn.memo == "produce"

    def test_writes_merchant_memory(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groceries")
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert merchant["categories_used"].get("cat-groceries") == 1
        assert str(c.txn.amount) in merchant["processings"]

    def test_snapshots_prior_state(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        original_payee_id = c.txn.payee_id
        engine.apply_category(c.id, category_id="cat-groceries")
        assert c.prior_state is not None
        assert c.prior_state["payee_id"] == original_payee_id
        assert c.prior_state["category_id"] is None  # was None pre-decision

    def test_unknown_candidate_id_raises(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        with pytest.raises(KeyError):
            engine.apply_category("not-a-real-id", category_id="cat-x")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplyCategory -v`
Expected: FAIL — `AttributeError: 'SyncEngine' object has no attribute 'apply_category'`.

- [ ] **Step 3: Implement apply_category in src/finab/engine/sync.py**

Inside the `SyncEngine` class, append:

```python
    def _candidate(self, candidate_id: str) -> Candidate:
        """Look up a candidate by id. Raises KeyError if not found."""
        for c in self.candidates:
            if c.id == candidate_id:
                return c
        raise KeyError(f"unknown candidate id: {candidate_id!r}")

    def _snapshot(self, txn) -> dict:
        """Capture the fields apply_*/undo cares about."""
        return {
            "category_id": getattr(txn, "category_id", None),
            "subtransactions": list(getattr(txn, "subtransactions", []) or []),
            "payee_id": getattr(txn, "payee_id", None),
            "payee_name": getattr(txn, "payee_name", None),
            "memo": getattr(txn, "memo", None),
        }

    def apply_category(
        self,
        candidate_id: str,
        *,
        category_id: str,
        memo: Optional[str] = None,
    ) -> None:
        """Record a single-category decision for the named candidate.

        Mutates the Transaction (category_id, subtransactions=[], optional memo),
        updates merchant memory (categories_used + processings) if the candidate
        has a resolvable merchant, and sets status='decided'.

        Memory update is by-design last-write-wins per (merchant, amount).
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        c.txn.category_id = category_id
        c.txn.subtransactions = []
        if memo is not None:
            c.txn.memo = memo
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplyCategory -v`
Expected: PASS — all four tests.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine.apply_category"
```

---

## Task 6: SyncEngine.apply_split

Same shape as `apply_category` but produces a multi-category split. The user supplies a list of `{category_id, amount_milliunits, memo}` dicts that sum to `txn.amount`. Engine writes them as `subtransactions` and clears `category_id`.

**Files:**
- Modify: `src/finab/engine/sync.py` (add `apply_split` method)
- Modify: `tests/engine/test_sync_engine.py` (add `TestApplySplit`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/engine/test_sync_engine.py`:

```python
class TestApplySplit:
    def _setup(self, tmp_path):
        # Same setup as TestApplyCategory
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-5", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        return engine, store

    def test_split_sets_subtransactions(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        c = engine.candidates[0]
        splits = [
            {"category_id": "cat-groc", "amount": -5000, "memo": "produce"},
            {"category_id": "cat-house", "amount": -3421, "memo": "soap"},
        ]
        engine.apply_split(c.id, splits=splits)
        assert c.status == "decided"
        assert c.txn.category_id is None
        assert len(c.txn.subtransactions) == 2
        assert c.txn.subtransactions[0]["amount"] == -5000

    def test_split_must_sum_to_total(self, tmp_path):
        engine, _ = self._setup(tmp_path)
        c = engine.candidates[0]
        bad_splits = [
            {"category_id": "cat-groc", "amount": -1000, "memo": ""},
            {"category_id": "cat-house", "amount": -1000, "memo": ""},
        ]
        with pytest.raises(ValueError, match="must sum"):
            engine.apply_split(c.id, splits=bad_splits)

    def test_split_writes_merchant_memory(self, tmp_path):
        engine, store = self._setup(tmp_path)
        c = engine.candidates[0]
        engine.apply_split(c.id, splits=[
            {"category_id": "cat-groc", "amount": -5000, "memo": ""},
            {"category_id": "cat-house", "amount": -3421, "memo": ""},
        ])
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        # both categories should be counted
        assert merchant["categories_used"].get("cat-groc") == 1
        assert merchant["categories_used"].get("cat-house") == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplySplit -v`
Expected: FAIL — `AttributeError: 'SyncEngine' object has no attribute 'apply_split'`.

- [ ] **Step 3: Implement apply_split**

Inside the `SyncEngine` class, append:

```python
    def apply_split(
        self,
        candidate_id: str,
        *,
        splits: list,
        memo: Optional[str] = None,
    ) -> None:
        """Record a multi-category split for the named candidate.

        `splits` is a list of {category_id, amount, memo} dicts. The sum
        of `amount` values must equal txn.amount; this is the same
        invariant the YNAB API enforces server-side, and surfacing it
        here lets the UI catch mistakes before flush.
        """
        c = self._candidate(candidate_id)
        total = sum(s["amount"] for s in splits)
        if total != c.txn.amount:
            raise ValueError(
                f"split amounts must sum to txn.amount "
                f"({c.txn.amount}); got {total}"
            )
        c.prior_state = self._snapshot(c.txn)
        c.txn.category_id = None
        c.txn.subtransactions = [
            {"category_id": s["category_id"], "amount": s["amount"], "memo": s.get("memo", "") or ""}
            for s in splits
        ]
        if memo is not None:
            c.txn.memo = memo
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplySplit -v`
Expected: PASS — all three tests.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine.apply_split with sum invariant"
```

---

## Task 7: SyncEngine.apply_transfer (manual override)

User force-marks a transaction as a transfer to one of their own accounts (when the auto-rule didn't fire because the merchant wasn't linked to an account's transfer payee). Engine sets `txn.payee_id` to the supplied transfer payee, clears category, sets `status = decided`. No merchant memory write — transfers aren't categorizations.

**Files:**
- Modify: `src/finab/engine/sync.py` (add `apply_transfer` method)
- Modify: `tests/engine/test_sync_engine.py` (add `TestApplyTransfer`)

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestApplyTransfer:
    def test_apply_transfer_sets_payee_and_clears_category(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-6", amount=-15000,
            account_id="fw-acc-1", merchant_id=None,
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        # Currently this txn is auto/no-merchant — override it to a transfer.
        engine.apply_transfer(c.id, transfer_payee_id="yn-tpayee-1")
        assert c.status == "decided"
        assert c.txn.payee_id == "yn-tpayee-1"
        assert c.txn.category_id is None
        assert c.txn.subtransactions == []

    def test_apply_transfer_does_not_touch_merchant_memory(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-7", amount=-9999,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        engine.apply_transfer(c.id, transfer_payee_id="yn-tpayee-1")
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert not merchant.get("categories_used")
        assert not merchant.get("processings")
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplyTransfer -v`
Expected: FAIL — `AttributeError: 'SyncEngine' object has no attribute 'apply_transfer'`.

- [ ] **Step 3: Implement apply_transfer**

Append inside the `SyncEngine` class:

```python
    def apply_transfer(
        self,
        candidate_id: str,
        *,
        transfer_payee_id: str,
    ) -> None:
        """Force-mark the candidate as a transfer to one of the user's
        own accounts. Used when the auto-rule didn't fire because the
        merchant wasn't linked to an account.

        Does NOT update merchant memory — transfers aren't categorizations.
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        c.txn.payee_id = transfer_payee_id
        c.txn.payee_name = None
        c.txn.category_id = None
        c.txn.subtransactions = []
        c.status = "decided"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestApplyTransfer -v`
Expected: PASS — both tests.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine.apply_transfer manual override"
```

---

## Task 8: SyncEngine.undo

Reverts a user decision: restores the Transaction's fields from the prior-state snapshot, sets `status = pending`, clears `prior_state`. Doesn't revert merchant memory (per spec — that's by design last-write-wins). Only valid on `status == "decided"`; raises on auto/pending/flushed.

**Files:**
- Modify: `src/finab/engine/sync.py`
- Modify: `tests/engine/test_sync_engine.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
class TestUndo:
    def _setup_decided(self, tmp_path):
        """Build an engine with one candidate, apply a category, return it."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-8", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groc", memo="weekly")
        return engine, c, store

    def test_undo_restores_prior_state(self, tmp_path):
        engine, c, _ = self._setup_decided(tmp_path)
        # Prior to apply_category, this candidate was pending with no category.
        engine.undo(c.id)
        assert c.status == "pending"
        assert c.txn.category_id is None
        assert c.prior_state is None

    def test_undo_does_not_revert_merchant_memory(self, tmp_path):
        engine, c, store = self._setup_decided(tmp_path)
        engine.undo(c.id)
        # Memory write from apply_category persists — by design.
        merchant = store.merchant_by_finwise_id("fw-merchant-2")
        assert merchant["categories_used"].get("cat-groc") == 1

    def test_undo_raises_on_auto_candidate(self, tmp_path):
        store = _seeded_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        txn = _build_txn(fw_uuid="fw-9", amount=-100, account_id="fw-acc-1", merchant_id=None)
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert c.status == "auto"
        with pytest.raises(ValueError, match="cannot undo"):
            engine.undo(c.id)
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestUndo -v`
Expected: FAIL — `AttributeError: 'SyncEngine' object has no attribute 'undo'`.

- [ ] **Step 3: Implement undo**

Append inside the `SyncEngine` class:

```python
    def undo(self, candidate_id: str) -> None:
        """Revert a user decision: status decided -> pending, restore
        snapshotted fields on txn. Does NOT revert merchant memory
        (last-write-wins by amount; an undo+re-decide just overwrites).
        """
        c = self._candidate(candidate_id)
        if c.status != "decided":
            raise ValueError(
                f"cannot undo candidate with status {c.status!r}; "
                f"only 'decided' supports undo"
            )
        if c.prior_state is None:
            raise ValueError(
                f"no prior_state recorded for candidate {candidate_id!r}"
            )
        snap = c.prior_state
        c.txn.category_id = snap["category_id"]
        c.txn.subtransactions = snap["subtransactions"]
        c.txn.payee_id = snap["payee_id"]
        c.txn.payee_name = snap["payee_name"]
        c.txn.memo = snap["memo"]
        c.prior_state = None
        c.status = "pending"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestUndo -v`
Expected: PASS — all three tests.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine.undo with prior-state restore"
```

---

## Task 9: SyncEngine.flush

Push all `decided` + `auto` candidates to YNAB. Partition by `txn.ynab_id` (creates vs. updates), call `YNABClient.create_transactions` / `update_transactions` in batches, and mark each pushed candidate `flushed` on success. Mirrors `_PendingQueue.flush` from `transactions.py:571-596`.

**Files:**
- Modify: `src/finab/engine/sync.py`
- Modify: `tests/engine/test_sync_engine.py`

- [ ] **Step 1: Write the failing tests**

Append:

```python
class _FakeYnabClient:
    """Tracks calls to create_transactions / update_transactions."""

    def __init__(self, fail_on=None):
        self.created = []  # list of lists
        self.updated = []
        self.fail_on = fail_on  # "create" or "update" to simulate failure

    def create_transactions(self, budget_id, txns):
        if self.fail_on == "create":
            raise RuntimeError("simulated create failure")
        self.created.append(list(txns))

    def update_transactions(self, budget_id, txns):
        if self.fail_on == "update":
            raise RuntimeError("simulated update failure")
        self.updated.append(list(txns))


class TestFlush:
    def _setup_engine_with_decisions(self, tmp_path, *, with_existing_ynab_id=False):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-10", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        if with_existing_ynab_id:
            txn.ynab_id = "yn-existing-123"
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        engine.apply_category(c.id, category_id="cat-groc")
        return engine, c

    def test_flush_pushes_creates(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path)
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.created) == 1
        assert client.created[0][0] is c.txn
        assert client.updated == []
        assert c.status == "flushed"

    def test_flush_pushes_updates(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path, with_existing_ynab_id=True)
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.updated) == 1
        assert client.updated[0][0] is c.txn
        assert client.created == []
        assert c.status == "flushed"

    def test_flush_skips_pending_and_flushed(self, tmp_path):
        """pending candidates and already-flushed ones aren't pushed."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        store.add_merchant(
            alias="Amazon",
            fw_record={"id": "fw-merchant-3", "name": "Amazon", "samples": []},
            ynab_record={"id": "yn-pay-3", "name": "Amazon", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        d = f"{today.year:04d}-{today.month:02d}-{today.day:02d}"
        txn_decided = _build_txn(fw_uuid="fw-11", amount=-1000, account_id="fw-acc-1", merchant_id="fw-merchant-2", date_str=d)
        txn_pending = _build_txn(fw_uuid="fw-12", amount=-2000, account_id="fw-acc-1", merchant_id="fw-merchant-3", date_str=d)
        engine = SyncEngine(
            fw_transactions=[txn_decided, txn_pending],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        # Decide one, leave the other pending.
        engine.apply_category(engine.candidates[0].id, category_id="cat-x")
        client = _FakeYnabClient()
        engine.flush(client, budget_id="bid")
        assert len(client.created[0]) == 1  # only the decided one
        # Second flush is a no-op (decided one is now flushed, other is still pending).
        engine.flush(client, budget_id="bid")
        assert len(client.created) == 1   # no new batches

    def test_flush_failure_leaves_candidates_in_pre_flush_state(self, tmp_path):
        engine, c = self._setup_engine_with_decisions(tmp_path)
        client = _FakeYnabClient(fail_on="create")
        with pytest.raises(RuntimeError, match="simulated"):
            engine.flush(client, budget_id="bid")
        assert c.status == "decided"  # not flushed
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestFlush -v`
Expected: FAIL — `AttributeError: 'SyncEngine' object has no attribute 'flush'`.

- [ ] **Step 3: Implement flush**

Append inside the `SyncEngine` class:

```python
    def flush(self, ynab_client, budget_id: str) -> None:
        """Push all decided + auto candidates to YNAB.

        Two batches: creates (no ynab_id) and updates (has ynab_id).
        Each batch's candidates are marked 'flushed' only after that
        batch's API call returns success — a partial failure (creates
        OK then updates raise) leaves the pre-failure batch flushed
        and the failing batch still 'decided' for retry.

        Raises on API failure (no swallowing).
        """
        # Snapshot which candidates we're attempting in this flush.
        pushable = [
            c for c in self.candidates
            if c.status in ("decided", "auto")
        ]
        creates = [c for c in pushable if not getattr(c.txn, "ynab_id", None)]
        updates = [c for c in pushable if getattr(c.txn, "ynab_id", None)]

        if creates:
            ynab_client.create_transactions(budget_id, [c.txn for c in creates])
            for c in creates:
                c.status = "flushed"
        if updates:
            ynab_client.update_transactions(budget_id, [c.txn for c in updates])
            for c in updates:
                c.status = "flushed"
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestFlush -v`
Expected: PASS — all four tests.

- [ ] **Step 5: Run the entire test suite**

Run: `uv run pytest`
Expected: still all green.

- [ ] **Step 6: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): SyncEngine.flush with partial-failure semantics"
```

---

## Task 10: Move phase-1 helpers to engine/accounts.py

Pull the pure pieces of phase 1 out of `main.py` so the future TUI Accounts screen can reuse them without dragging in the CLI's prompt code. The functions moved here are all already pure-ish (they may call clients/stores, but no `input()` and no ANSI colour).

**Files:**
- Create: `src/finab/engine/accounts.py`
- Create: `tests/engine/test_accounts_helpers.py`
- Modify: `src/finab/main.py` (delete moved bodies, add re-export block)

**Names moved:**
- `_calculate_starting_balance`
- `_account_with_overrides`
- `_reconcile_store_accounts_to_ynab`

**Names that STAY in main.py** (interactive or top-level orchestration):
- `_color`, `_bold`, `_dim`, `_red`, `_green`, `_yellow`, `_cyan`
- `_prompt_alias_required`, `_prompt_yes_no`, `_gather_pickable_entries`, `_interactive_pick`, `_prompt_alias_with_picker`
- `_link_account_transfer_payee` (used by both phase 1 and phase 2 — moves to merchants.py in Task 11)
- `_record_merchant_alias` (used at sync time)
- `_extract_distinct_merchants` (moves to merchants.py in Task 11)
- `_reconcile_store_merchants_to_ynab` (moves to merchants.py in Task 11)
- `sync_accounts`, `sync_merchants`, `main`

- [ ] **Step 1: Write the failing smoke test**

Create `tests/engine/test_accounts_helpers.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_accounts_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.engine.accounts'`.

- [ ] **Step 3: Create src/finab/engine/accounts.py**

Create the file with this header and the bodies of the three named functions copied verbatim from `main.py`:

```python
"""Pure helpers for phase 1 (account sync).

Extracted from finab.main to give the (future) TUI Accounts screen a
non-interactive surface. main.py re-exports them so existing call
sites keep working.

No interactive I/O here — these helpers may call clients/stores but
never call input() or use ANSI colour.
"""
from datetime import date


# --- Below: paste the bodies of _calculate_starting_balance,
# _account_with_overrides, _reconcile_store_accounts_to_ynab from
# main.py exactly as they appear at lines 118-336.
```

Three caveats during the paste:

1. `_reconcile_store_accounts_to_ynab` calls `print(...)` for diagnostic output (e.g., `print(f"  Recreated YNAB account '{name}'")`). The engine-pure rule says no print. For Plan 1, **keep the prints in place** — we accept that this function isn't perfectly pure today. Plans 2/3 will refactor it to emit events or return a result object once the TUI needs structured output. Don't fight this in Plan 1; the goal is a clean module split, not a perfect purity guarantee on day one. Add a one-line comment above the prints noting "TODO(plan-2): replace with structured result for TUI".

2. `_reconcile_store_accounts_to_ynab` imports `Account` inside its body (`from finab.models import Account`). Keep that local import — it's already there to avoid a circular import.

3. `_calculate_starting_balance` uses `fw_client.get_transactions(start_date=start_date)` with a `start_date` kwarg. Don't add type hints that would break this — the client method signature is what we're working with.

- [ ] **Step 4: Edit src/finab/main.py — remove the moved bodies and add a re-export block**

Delete the three functions from `main.py` and add this block immediately under the existing imports at the top of the file:

```python
# --- Re-exports from finab.engine.accounts ---
# These helpers moved to finab.engine.accounts; re-exported here so
# existing call sites in main.py and tests keep working.
from finab.engine.accounts import (
    _calculate_starting_balance,
    _account_with_overrides,
    _reconcile_store_accounts_to_ynab,
)
```

- [ ] **Step 5: Run the smoke tests**

Run: `uv run pytest tests/engine/test_accounts_helpers.py -v`
Expected: PASS — both tests.

- [ ] **Step 6: Run the entire test suite**

Run: `uv run pytest`
Expected: every test that was previously passing continues to pass. Specifically, `tests/test_sync_accounts.py` exercises `sync_accounts` end-to-end and depends on these helpers being callable through `main.py`'s namespace.

- [ ] **Step 7: Commit**

```bash
git add src/finab/engine/accounts.py src/finab/main.py tests/engine/test_accounts_helpers.py
git commit -m "refactor(engine): move pure phase-1 helpers to engine/accounts.py"
```

---

## Task 11: Move phase-2 helpers to engine/merchants.py

Same shape as Task 10, but for phase 2. `_link_account_transfer_payee` lives here too — even though it's also called during phase 1's `sync_accounts` flow when a merchant alias matches an account, it's logically about merchant linkage and the future TUI Merchants screen needs it.

**Files:**
- Create: `src/finab/engine/merchants.py`
- Create: `tests/engine/test_merchants_helpers.py`
- Modify: `src/finab/main.py`

**Names moved:**
- `_link_account_transfer_payee`
- `_extract_distinct_merchants`
- `_reconcile_store_merchants_to_ynab`
- `_record_merchant_alias` (used by phase 3 as a defensive fallback; pure-ish call into client + store)

**Names that STAY in main.py:**
- Everything else (interactive prompts, `sync_accounts`, `sync_merchants`, `main`).

- [ ] **Step 1: Write the failing smoke test**

Create `tests/engine/test_merchants_helpers.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/engine/test_merchants_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.engine.merchants'`.

- [ ] **Step 3: Create src/finab/engine/merchants.py**

Create with header:

```python
"""Pure helpers for phase 2 (merchant sync).

Extracted from finab.main to give the (future) TUI Merchants screen
a non-interactive surface. main.py re-exports them so existing call
sites keep working.
"""
from typing import Optional

from finab.store import ConfigStore, to_dict, normalize_alias
from finab.client import FinWiseClient  # noqa: F401 (kept for symmetry with main.py)
from finab.ynab_client import YNABClient


# --- Below: paste the bodies of _link_account_transfer_payee,
# _extract_distinct_merchants, _reconcile_store_merchants_to_ynab,
# _record_merchant_alias from main.py exactly as they appear at
# lines 131-204 and 207-262 and 339-366.
```

Same `print()`-in-`_reconcile_*` caveat as Task 10 — keep the prints, add a TODO comment, defer the cleanup to Plan 2.

- [ ] **Step 4: Edit src/finab/main.py — remove moved bodies, add re-export block**

Delete the four functions from `main.py` and add to the re-export block (after the accounts re-exports from Task 10):

```python
# --- Re-exports from finab.engine.merchants ---
from finab.engine.merchants import (
    _link_account_transfer_payee,
    _extract_distinct_merchants,
    _reconcile_store_merchants_to_ynab,
    _record_merchant_alias,
)
```

- [ ] **Step 5: Run the smoke tests**

Run: `uv run pytest tests/engine/test_merchants_helpers.py -v`
Expected: PASS — both tests.

- [ ] **Step 6: Run the entire test suite**

Run: `uv run pytest`
Expected: every test passes. `tests/test_sync_merchants.py` exercises these helpers end-to-end via `sync_merchants` from `main.py`.

- [ ] **Step 7: Commit**

```bash
git add src/finab/engine/merchants.py src/finab/main.py tests/engine/test_merchants_helpers.py
git commit -m "refactor(engine): move pure phase-2 helpers to engine/merchants.py"
```

---

## Task 12: Final verification

End-to-end sanity check: run the full test suite, then a manual smoke of `uv run finab` to confirm the CLI still works exactly as before.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -v`
Expected: every test that was passing on the starting commit still passes, plus the new tests in `tests/engine/`. Zero regressions.

- [ ] **Step 2: Manual smoke test — start the CLI**

Run: `uv run finab`
Expected: the CLI behaves identically to the pre-plan baseline — same prompts, same colour output, same flow through phase 1 / 2 / 3. Walk through at least one transaction (categorize or skip) to confirm `_process_one_transaction` still works through the re-exports.

If anything looks different from the baseline (missing prompt, missing colour on a previously-coloured line that *wasn't* in the `merge_and_filter_transactions` dedup diagnostics), check whether a moved name lost its colour wrappers during the move. Fix in `engine/sync.py` and re-test.

- [ ] **Step 3: Inspect the module structure**

Run: `ls -la src/finab/engine/ && wc -l src/finab/engine/*.py src/finab/transactions.py src/finab/main.py`
Expected: four files in `src/finab/engine/` (`__init__.py`, `sync.py`, `accounts.py`, `merchants.py`); `transactions.py` and `main.py` are both meaningfully shorter than they were before.

- [ ] **Step 4: Confirm no Textual or interactive I/O leaked into engine/**

Run: `grep -rn "input(\|print(\|textual\|^from textual" src/finab/engine/ || true`
Expected: zero matches for `input(` and `textual`. The `print()` calls inside `_reconcile_*` functions are expected and have TODO comments — note them but don't fail. If anything else turns up, move it back or refactor it.

- [ ] **Step 5: No commit needed — verification only**

No code change in this task; nothing to commit. Plan 1 is complete.

---

## Self-Review

Walked the spec sections against this plan:

- **Spec §Architecture → engine/ + tui/ subpackages.** Plan 1 creates `engine/`; `tui/` is correctly deferred to Plan 2.
- **Spec §Engine contract → SyncEngine API (`load_candidates`, `apply`, `undo`, `flush`).** Implemented as `__init__`(implicit load via constructor), `apply_category` / `apply_split` / `apply_transfer`, `undo`, `flush`. Naming differs from the spec ("apply" → three named methods) — this is a deliberate refinement: a single `apply(decision)` polymorphic over Decision dataclasses would force the engine to know all decision shapes, which is more ceremony than the three named methods.
- **Spec §Candidate state machine (`pending → decided/auto → flushed`).** Implemented in `Candidate.status`. Tested.
- **Spec §Other screens.** Out of scope for Plan 1 (Plan 2).
- **Spec §Data flow & persistence.** Engine's data flow matches: apply writes memory immediately; flush is the only YNAB call; mappings are written by `merge_and_filter_transactions` before push.
- **Spec §Error handling §Flush failures.** Tested in `TestFlush.test_flush_failure_leaves_candidates_in_pre_flush_state`.
- **Spec §Testing §Engine.** Covered.
- **Spec §Migration plan steps 1–3.** Covered by Tasks 1, 2 (step 1), 3–9 (step 2), 10–11 (step 3).

**Placeholder scan:** No "TBD", no "TODO" in step bodies. One "TODO(plan-2)" code comment in `_reconcile_*` functions, deliberate and documented as a follow-up.

**Type consistency:** `Candidate.id` is a `str` everywhere. `CandidateStatus` literal types match across tests and impl. `apply_*` methods all take `candidate_id: str` as first positional arg. `flush(ynab_client, budget_id)` signature matches `_PendingQueue.flush` from existing `transactions.py:571`. The `apply_split` `splits` parameter uses key `"amount"` (matches `Transaction.subtransactions` keys throughout the codebase, see `_update_merchant_memory` in `transactions.py:638-686` and `_apply_processing_to_txn` in `transactions.py:770-802`); this is intentional, not a clash with `"amount_milliunits"` (which is the key in stored merchant memory `processings`).

**One real gap caught during review:** The spec says `engine.flush()` should mark candidates `flushed` after a successful batch. Earlier draft of this plan had the per-batch loop emitting `flushed` before partial-failure handling. Re-read carefully and the current Task 9 impl does mark them in the right place (after each batch succeeds, before the next batch is attempted). Verified against the test `test_flush_failure_leaves_candidates_in_pre_flush_state`. OK.

---
