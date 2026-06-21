# Lifecycle + enablers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make finab self-sufficient — first-run budget picker, in-app refresh with a flush-or-discard guard, reconcile-to-YNAB from the TUI with structured results, Settings budget-switching — plus the small enablers (parallel load, dead-code removal) those depend on.

**Architecture:** Both boot and refresh funnel through one re-load path; the existing `_kickoff_load` worker (which already re-binds every screen and rebuilds the Sync engine) is split into a reusable `_load_and_bind` coroutine. Reconcile logic stays pure (engine) and returns a `ReconcileResult` dataclass that the TUI renders in a modal. New modals mirror the existing `ModalScreen` picker pattern exactly.

**Tech Stack:** Python 3.14, Textual 4.x (TUI + Pilot tests), pytest + pytest-asyncio, the `ynab` and `finwise-python` SDKs (wrapped by `YNABClient` / `FinWiseClient`).

**Branch:** `lifecycle-enablers` (already created; the design spec is committed there).

---

## File structure

**Create:**
- `src/finab/engine/reconcile.py` — `ReconcileResult` dataclass (shared by both reconcile helpers).
- `src/finab/tui/widgets/reconcile_result.py` — `ReconcileResultModal` (renders a `ReconcileResult`).
- `src/finab/tui/widgets/budget_picker.py` — `BudgetPickerModal` (fuzzy-search modal over fetched budgets).
- `tests/engine/test_reconcile.py` — behavioral tests for the two reconcile helpers + `ReconcileResult`.
- `tests/tui/test_reconcile_result.py` — modal tests.
- `tests/tui/test_budget_picker.py` — modal tests.
- `tests/tui/test_bootstrap.py` — app boot/budget-resolution + refresh + switch-budget tests.

**Modify:**
- `src/finab/tui/screens/placeholder.py` — delete (F3).
- `src/finab/tui/app.py` — remove placeholder import (F3); bootstrap + refresh + reconcile-dispatch + switch-budget (A1/A2/A3/B1).
- `src/finab/tui/data_loader.py` — parallelize `load_all` (F2).
- `src/finab/engine/accounts.py` — `_reconcile_store_accounts_to_ynab` returns `ReconcileResult` (F1).
- `src/finab/engine/merchants.py` — `_reconcile_store_merchants_to_ynab` returns `ReconcileResult` (F1).
- `src/finab/tui/screens/accounts.py` — `action_reconcile` (A3).
- `src/finab/tui/screens/merchants.py` — `action_reconcile` (A3).

---

## Task 1: Remove the dead PlaceholderScreen (F3)

**Files:**
- Delete: `src/finab/tui/screens/placeholder.py`
- Modify: `src/finab/tui/app.py:5` (docstring), `src/finab/tui/app.py:18` (import)
- Test: `tests/tui/test_app.py` (existing — must still pass)

- [ ] **Step 1: Confirm nothing else imports PlaceholderScreen**

Run: `grep -rn "placeholder\|PlaceholderScreen" src tests`
Expected: only `src/finab/tui/app.py:5` (docstring) and `:18` (import). No other references.

- [ ] **Step 2: Delete the module and its import**

```bash
git rm src/finab/tui/screens/placeholder.py
```

In `src/finab/tui/app.py`, delete line 18:

```python
from finab.tui.screens.placeholder import PlaceholderScreen
```

And edit the module docstring (line 5) — replace:

```python
on that data; placeholder screens don't care.
```

with:

```python
on that data; other screens render from local state.
```

- [ ] **Step 3: Run the app test suite to verify nothing broke**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: PASS (all existing tests green; import of `PlaceholderScreen` no longer attempted).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "$(cat <<'EOF'
chore(tui): remove dead PlaceholderScreen (F3)

All five screens are real now; the placeholder module and its import
were leftover from Plan 3.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Parallelize load_all (F2)

**Files:**
- Modify: `src/finab/tui/data_loader.py`
- Test: `tests/tui/test_data_loader.py` (existing — extend)

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_data_loader.py`:

```python
@pytest.mark.asyncio
async def test_load_all_returns_full_bundle():
    from finab.tui.data_loader import load_all

    class _Fw:
        def get_accounts(self): return ["fw-acc"]
        def get_transactions(self): return ["fw-txn"]

    class _Yn:
        def get_accounts(self, b): return ["yn-acc"]
        def get_transactions(self, b): return ["yn-txn"]
        def get_categories(self, b): return ["cat"]
        def get_category_groups_with_categories(self, b): return ["grp"]
        def get_payees(self, b): return ["payee"]

    data = await load_all(fw_client=_Fw(), ynab_client=_Yn(), budget_id="B")
    assert data.error is None
    assert data.fw_accounts == ["fw-acc"]
    assert data.fw_transactions == ["fw-txn"]
    assert data.ynab_accounts == ["yn-acc"]
    assert data.ynab_transactions == ["yn-txn"]
    assert data.ynab_categories == ["cat"]
    assert data.ynab_category_groups == ["grp"]
    assert data.ynab_payees == ["payee"]


@pytest.mark.asyncio
async def test_load_all_surfaces_first_error():
    from finab.tui.data_loader import load_all

    class _Fw:
        def get_accounts(self): raise RuntimeError("boom")
        def get_transactions(self): return []

    class _Yn:
        def get_accounts(self, b): return []
        def get_transactions(self, b): return []
        def get_categories(self, b): return []
        def get_category_groups_with_categories(self, b): return []
        def get_payees(self, b): return []

    data = await load_all(fw_client=_Fw(), ynab_client=_Yn(), budget_id="B")
    assert isinstance(data.error, RuntimeError)
    assert str(data.error) == "boom"
```

Ensure `import pytest` is present at the top of the file (it is, if other async tests exist; add it otherwise).

- [ ] **Step 2: Run tests to verify they pass against the current (sequential) implementation**

Run: `uv run pytest tests/tui/test_data_loader.py -k "full_bundle or first_error" -v`
Expected: PASS — these tests describe behavior that must hold both before and after parallelization (they lock the contract so the rewrite can't regress it).

- [ ] **Step 3: Rewrite load_all to fetch in parallel**

Replace the body of `load_all` in `src/finab/tui/data_loader.py` (lines 30-48) with:

```python
async def load_all(*, fw_client, ynab_client, budget_id: str) -> LoadedData:
    """Fetch everything the TUI needs on boot, concurrently. Returns LoadedData.

    The SDK clients are synchronous (httpx under the hood), so each call is
    offloaded to a thread and the seven run concurrently via asyncio.gather.
    On any exception, gather re-raises the first one; we catch it and return
    LoadedData with `error` populated (partial fields stay at their defaults).
    """
    import asyncio

    data = LoadedData()
    try:
        (
            data.fw_accounts,
            data.fw_transactions,
            data.ynab_accounts,
            data.ynab_transactions,
            data.ynab_categories,
            data.ynab_category_groups,
            data.ynab_payees,
        ) = await asyncio.gather(
            asyncio.to_thread(fw_client.get_accounts),
            asyncio.to_thread(fw_client.get_transactions),
            asyncio.to_thread(ynab_client.get_accounts, budget_id),
            asyncio.to_thread(ynab_client.get_transactions, budget_id),
            asyncio.to_thread(ynab_client.get_categories, budget_id),
            asyncio.to_thread(ynab_client.get_category_groups_with_categories, budget_id),
            asyncio.to_thread(ynab_client.get_payees, budget_id),
        )
    except Exception as e:
        data.error = e
    return data
```

Also update the module docstring (lines 1-12) to drop the "Sequential, not parallel" paragraph and state that fetches now run concurrently via `asyncio.to_thread` + `asyncio.gather`.

- [ ] **Step 4: Run the full data_loader suite**

Run: `uv run pytest tests/tui/test_data_loader.py -v`
Expected: PASS (new + existing tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/tui/data_loader.py tests/tui/test_data_loader.py
git commit -m "$(cat <<'EOF'
perf(tui): parallelize load_all fetches with asyncio.gather (F2)

Offload the seven synchronous SDK calls to threads and run them
concurrently, as the loader docstring already recommended.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: ReconcileResult dataclass + restructure reconcile helpers (F1)

**Files:**
- Create: `src/finab/engine/reconcile.py`
- Modify: `src/finab/engine/accounts.py:43-107`, `src/finab/engine/merchants.py:155-185`
- Test: `tests/engine/test_reconcile.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/engine/test_reconcile.py`:

```python
"""Behavioral tests for the reconcile helpers and their structured result.

These are the first behavior tests for the reconcile functions (previously
they only had import/callable smoke tests). They use lightweight fakes for
the store and the YNAB client.
"""


class _AccObj:
    def __init__(self, id, name, type="checking", balance=0, transfer_payee_id=None):
        self.id = id
        self.name = name
        self.type = type
        self.balance = balance
        self.transfer_payee_id = transfer_payee_id


class _AccResp:
    def __init__(self, account):
        self.data = type("D", (), {"account": account})()


class _PayeeObj:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _FakeStore:
    def __init__(self, accounts=None, merchants=None):
        self._accounts = accounts or []
        self._merchants = merchants or []
        self.saved = []

    def accounts(self):
        return list(self._accounts)

    def merchants(self):
        return list(self._merchants)

    def set_account_ynab_record(self, entry_id, record):
        self.saved.append((entry_id, record))

    def set_merchant_ynab_record(self, entry_id, record):
        self.saved.append((entry_id, record))


class _FakeYnab:
    def __init__(self, fail=False):
        self.fail = fail
        self.created = []

    def create_account(self, budget_id, payload):
        if self.fail:
            raise RuntimeError("api down")
        self.created.append(payload.name)
        return _AccResp(_AccObj(id="new-" + payload.name, name=payload.name,
                                type=payload.type, balance=payload.balance))

    def create_payee(self, budget_id, name):
        if self.fail:
            raise RuntimeError("api down")
        self.created.append(name)
        return _PayeeObj(id="pay-" + name, name=name)


def test_reconcile_accounts_creates_missing_and_returns_result():
    from finab.engine.accounts import _reconcile_store_accounts_to_ynab
    from finab.engine.reconcile import ReconcileResult

    store = _FakeStore(accounts=[
        {"id": "e1", "alias": "Chequing",
         "finwise": {"id": "fw1", "type": "checking", "balance": 0, "currency_code": "ZAR"},
         "ynab": {}},  # no ynab id -> must create
    ])
    yn = _FakeYnab()

    result = _reconcile_store_accounts_to_ynab(store, ynab_accounts=[], ynab_client=yn, budget_id="B")

    assert isinstance(result, ReconcileResult)
    assert result.created == ["Chequing"]
    assert result.skipped == []
    assert result.failed == []
    assert yn.created == ["Chequing"]
    assert store.saved and store.saved[0][0] == "e1"


def test_reconcile_accounts_skips_already_present():
    from finab.engine.accounts import _reconcile_store_accounts_to_ynab

    store = _FakeStore(accounts=[
        {"id": "e1", "alias": "Chequing", "finwise": {}, "ynab": {"id": "yn-1"}},
    ])
    yn = _FakeYnab()
    result = _reconcile_store_accounts_to_ynab(
        store, ynab_accounts=[_AccObj(id="yn-1", name="Chequing")],
        ynab_client=yn, budget_id="B",
    )
    assert result.created == []
    assert yn.created == []


def test_reconcile_accounts_records_failure():
    from finab.engine.accounts import _reconcile_store_accounts_to_ynab

    store = _FakeStore(accounts=[
        {"id": "e1", "alias": "Chequing", "finwise": {}, "ynab": {}},
    ])
    yn = _FakeYnab(fail=True)
    result = _reconcile_store_accounts_to_ynab(store, ynab_accounts=[], ynab_client=yn, budget_id="B")
    assert result.created == []
    assert result.failed and result.failed[0][0] == "Chequing"
    assert "api down" in result.failed[0][1]


def test_reconcile_accounts_skips_entry_with_no_name():
    from finab.engine.accounts import _reconcile_store_accounts_to_ynab

    store = _FakeStore(accounts=[
        {"id": "e1", "alias": None, "finwise": {}, "ynab": {}},
    ])
    yn = _FakeYnab()
    result = _reconcile_store_accounts_to_ynab(store, ynab_accounts=[], ynab_client=yn, budget_id="B")
    assert result.created == []
    assert result.skipped and result.skipped[0][0] == "e1"


def test_reconcile_merchants_creates_missing_and_returns_result():
    from finab.engine.merchants import _reconcile_store_merchants_to_ynab
    from finab.engine.reconcile import ReconcileResult

    store = _FakeStore(merchants=[
        {"id": "m1", "alias": "Woolworths", "finwise": {"fw1": {}}, "ynab": {}},
    ])
    yn = _FakeYnab()
    result = _reconcile_store_merchants_to_ynab(store, ynab_payees=[], ynab_client=yn, budget_id="B")

    assert isinstance(result, ReconcileResult)
    assert result.created == ["Woolworths"]
    assert yn.created == ["Woolworths"]
    assert store.saved and store.saved[0][0] == "m1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: finab.engine.reconcile` / current helpers return `int`, not `ReconcileResult`.

- [ ] **Step 3: Create the ReconcileResult dataclass**

Create `src/finab/engine/reconcile.py`:

```python
"""Structured result for the phase-1/2 reconcile helpers.

The reconcile functions recreate YNAB-side records for store entries whose
YNAB counterpart has gone missing. They run inside a TUI, so they return
this structured result instead of printing — the caller renders it.
"""
from dataclasses import dataclass, field


@dataclass
class ReconcileResult:
    """Outcome of a reconcile pass.

    created: names of records created on YNAB.
    skipped: (entry_id, reason) for entries that couldn't be pushed.
    failed:  (name, error_message) for entries whose API call raised.
    """
    created: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """One-line human summary, e.g. '2 created, 1 skipped, 0 failed'."""
        return (
            f"{len(self.created)} created, "
            f"{len(self.skipped)} skipped, "
            f"{len(self.failed)} failed"
        )
```

- [ ] **Step 4: Rewrite `_reconcile_store_accounts_to_ynab` to return it**

In `src/finab/engine/accounts.py`, add the import near the top (after the existing `from typing import ...`):

```python
from finab.engine.reconcile import ReconcileResult
```

Replace the whole function body (lines 43-107) so it builds and returns a `ReconcileResult` instead of `print()`-ing and counting. The new body:

```python
def _reconcile_store_accounts_to_ynab(
    store: "ConfigStore",
    ynab_accounts,
    ynab_client: "YNABClient",
    budget_id: str,
) -> ReconcileResult:
    """For each account entry in the store, ensure its YNAB-side counterpart
    exists in YNAB. Create on YNAB and update the store entry if missing.
    Returns a ReconcileResult describing created / skipped / failed entries."""
    existing_ids = {
        str(getattr(a, "ynab_id", None) or getattr(a, "id", ""))
        for a in ynab_accounts
        if (getattr(a, "ynab_id", None) or getattr(a, "id", None))
    }
    result = ReconcileResult()
    for entry in list(store.accounts()):
        yn = entry.get("ynab", {})
        yn_id = yn.get("id")
        if yn_id and str(yn_id) in existing_ids:
            continue

        name = entry.get("alias") or yn.get("name")
        if not name:
            result.skipped.append((entry.get("id"), "no alias/name to push"))
            continue
        fw = entry.get("finwise", {}) or {}
        acc_type = yn.get("type") or fw.get("type") or "checking"
        balance = yn.get("balance")
        if balance is None:
            balance = fw.get("balance", 0)
        currency = fw.get("currency_code", "")

        try:
            from finab.models import Account
            payload = Account(
                name=name,
                type=acc_type,
                balance=int(balance) if balance is not None else 0,
                currency_code=currency,
            )
            response = ynab_client.create_account(budget_id, payload)
            new_record = response.data.account
            store.set_account_ynab_record(
                entry["id"],
                {
                    "id": str(getattr(new_record, "id", "")),
                    "name": getattr(new_record, "name", name),
                    "type": getattr(new_record, "type", acc_type),
                    "balance": getattr(new_record, "balance", balance),
                    "transfer_payee_id": (
                        str(new_record.transfer_payee_id)
                        if getattr(new_record, "transfer_payee_id", None) is not None
                        else None
                    ),
                },
            )
            result.created.append(name)
        except Exception as e:
            result.failed.append((name, str(e)))
    return result
```

Also update the module docstring (lines 1-11) to drop the "Diagnostic print() calls … TODO(plan-2)" paragraph — there are no more prints.

- [ ] **Step 5: Rewrite `_reconcile_store_merchants_to_ynab` to return it**

In `src/finab/engine/merchants.py`, add after the existing imports:

```python
from finab.engine.reconcile import ReconcileResult
```

Replace the function body (lines 155-185) with:

```python
def _reconcile_store_merchants_to_ynab(
    store: "ConfigStore",
    ynab_payees,
    ynab_client: "YNABClient",
    budget_id: str,
) -> ReconcileResult:
    """For each merchant entry in the store, ensure its YNAB payee exists in
    YNAB. Create and update the store entry if missing. Returns a
    ReconcileResult describing created / skipped / failed entries."""
    existing_ids = {str(p.id) for p in ynab_payees if getattr(p, "id", None) is not None}
    result = ReconcileResult()
    for entry in list(store.merchants()):
        yn = entry.get("ynab", {})
        yn_id = yn.get("id")
        if yn_id and str(yn_id) in existing_ids:
            continue

        name = entry.get("alias") or yn.get("name")
        if not name:
            result.skipped.append((entry.get("id"), "no alias/name to push"))
            continue
        try:
            new_payee = ynab_client.create_payee(budget_id, name)
            store.set_merchant_ynab_record(entry["id"], to_dict(new_payee))
            result.created.append(name)
        except Exception as e:
            result.failed.append((name, str(e)))
    return result
```

Also trim the module docstring's "Diagnostic print() … TODO(plan-2)" paragraph.

- [ ] **Step 6: Run the reconcile tests + existing smoke tests**

Run: `uv run pytest tests/engine/test_reconcile.py tests/engine/test_accounts_helpers.py tests/engine/test_merchants_helpers.py -v`
Expected: PASS (new behavior tests green; smoke `assert callable` tests still green).

- [ ] **Step 7: Commit**

```bash
git add src/finab/engine/reconcile.py src/finab/engine/accounts.py src/finab/engine/merchants.py tests/engine/test_reconcile.py
git commit -m "$(cat <<'EOF'
feat(engine): reconcile helpers return ReconcileResult (F1)

Replace the TODO(plan-2) print() diagnostics with a structured
ReconcileResult (created/skipped/failed) the TUI can render. Adds the
first behavioral tests for these helpers.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: ReconcileResultModal widget

**Files:**
- Create: `src/finab/tui/widgets/reconcile_result.py`
- Test: `tests/tui/test_reconcile_result.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_reconcile_result.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_modal_renders_summary_and_dismisses_on_enter():
    from textual.app import App
    from finab.engine.reconcile import ReconcileResult
    from finab.tui.widgets.reconcile_result import ReconcileResultModal

    result = ReconcileResult(created=["Chequing"], skipped=[], failed=[])
    closed = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                ReconcileResultModal(result=result, title="Accounts reconcile"),
                callback=lambda r: closed.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        body = app.screen.query_one("#reconcile-result-body")
        assert "1 created" in str(body.render())
        await pilot.press("enter")
        await pilot.pause()
    assert closed["value"] is None  # modal dismisses (no payload)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/tui/test_reconcile_result.py -v`
Expected: FAIL — module `finab.tui.widgets.reconcile_result` does not exist.

- [ ] **Step 3: Implement the modal**

Create `src/finab/tui/widgets/reconcile_result.py`:

```python
"""ReconcileResultModal — shows the outcome of a reconcile pass.

Renders a ReconcileResult (created / skipped / failed) and dismisses on
Enter or Escape. Dismisses with None — there's no payload to return.
"""
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from finab.engine.reconcile import ReconcileResult


class ReconcileResultModal(ModalScreen[None]):
    """Read-only summary of a reconcile pass."""

    BINDINGS = [
        ("enter", "dismiss(None)", "OK"),
        ("escape", "dismiss(None)", "OK"),
    ]

    def __init__(self, *, result: ReconcileResult, title: str = "Reconcile"):
        super().__init__()
        self._result = result
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="reconcile-result-dialog"):
            yield Static(self._title, id="reconcile-result-title")
            yield Static(self._body_text(), id="reconcile-result-body")
            yield Static("  Enter / Esc — close", id="reconcile-result-footer")

    def _body_text(self) -> str:
        r = self._result
        lines = [f"  {r.summary}", ""]
        for name in r.created:
            lines.append(f"  + created  {name}")
        for entry_id, reason in r.skipped:
            lines.append(f"  ~ skipped  {entry_id} ({reason})")
        for name, err in r.failed:
            lines.append(f"  ✗ failed   {name}: {err}")
        return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/tui/test_reconcile_result.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/tui/widgets/reconcile_result.py tests/tui/test_reconcile_result.py
git commit -m "$(cat <<'EOF'
feat(tui): add ReconcileResultModal to display reconcile outcomes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Wire reconcile into Accounts + Merchants screens and the app (A3)

**Files:**
- Modify: `src/finab/tui/screens/accounts.py` (add `action_reconcile`), `src/finab/tui/screens/merchants.py` (add `action_reconcile`)
- Modify: `src/finab/tui/app.py` (binding, dispatch, `check_action` scoping)
- Test: `tests/tui/test_accounts_screen.py`, `tests/tui/test_merchants_screen.py`, `tests/tui/test_app.py`

- [ ] **Step 1: Write the failing screen tests**

Add to `tests/tui/test_accounts_screen.py`:

```python
@pytest.mark.asyncio
async def test_accounts_reconcile_pushes_result_modal():
    from textual.app import App
    from finab.tui.screens.accounts import AccountsScreen
    from finab.tui.widgets.reconcile_result import ReconcileResultModal

    class _FakeStore:
        def accounts(self): return [
            {"id": "e1", "alias": "Chequing", "finwise": {}, "ynab": {}},
        ]
        def set_account_ynab_record(self, *a, **k): pass

    class _AccObj:
        def __init__(self, id, name): self.id = id; self.name = name; self.type = "checking"; self.balance = 0; self.transfer_payee_id = None

    class _Resp:
        def __init__(self, acc): self.data = type("D", (), {"account": acc})()

    class _FakeYnab:
        def create_account(self, b, payload): return _Resp(_AccObj("new", payload.name))

    class _Host(App):
        def compose(self):
            yield AccountsScreen(id="screen-accounts")
        def on_mount(self):
            scr = self.query_one(AccountsScreen)
            scr.bind_data(store=_FakeStore(), fw_accounts=[], ynab_accounts=[],
                          ynab_client=_FakeYnab(), budget_id="B")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one(AccountsScreen).action_reconcile()
        await pilot.pause()
        assert isinstance(app.screen, ReconcileResultModal)
```

Add the analogous test to `tests/tui/test_merchants_screen.py` (use `_FakeStore.merchants()` returning `[{"id": "m1", "alias": "Woolworths", "finwise": {"fw1": {}}, "ynab": {}}]`, `set_merchant_ynab_record`, a `_FakeYnab.create_payee(self, b, name)` returning an object with `.id`/`.name`, and `bind_data(store=..., fw_transactions=[], ynab_payees=[], ynab_client=..., budget_id="B")`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tui/test_accounts_screen.py -k reconcile tests/tui/test_merchants_screen.py -k reconcile -v`
Expected: FAIL — `AccountsScreen` / `MerchantsScreen` has no `action_reconcile`.

- [ ] **Step 3: Add `action_reconcile` to AccountsScreen**

Append to `src/finab/tui/screens/accounts.py` (new method on `AccountsScreen`):

```python
    def action_reconcile(self) -> None:
        """Recreate any store accounts missing on YNAB, then show the result."""
        if self._store is None or self._ynab_client is None or self._budget_id is None:
            self.app.bell()
            return
        from finab.engine.accounts import _reconcile_store_accounts_to_ynab
        from finab.tui.widgets.reconcile_result import ReconcileResultModal
        result = _reconcile_store_accounts_to_ynab(
            self._store, self._ynab_accounts, self._ynab_client, self._budget_id
        )
        self.refresh_rows()
        self.app.push_screen(ReconcileResultModal(result=result, title="Accounts reconcile"))
```

- [ ] **Step 4: Add `action_reconcile` to MerchantsScreen**

Append to `src/finab/tui/screens/merchants.py`:

```python
    def action_reconcile(self) -> None:
        """Recreate any store merchants missing on YNAB, then show the result."""
        if self._store is None or self._ynab_client is None or self._budget_id is None:
            self.app.bell()
            return
        from finab.engine.merchants import _reconcile_store_merchants_to_ynab
        from finab.tui.widgets.reconcile_result import ReconcileResultModal
        result = _reconcile_store_merchants_to_ynab(
            self._store, self._ynab_payees, self._ynab_client, self._budget_id
        )
        self.refresh_rows()
        self.app.push_screen(ReconcileResultModal(result=result, title="Merchants reconcile"))
```

- [ ] **Step 5: Add the app binding, dispatch, and check_action scoping**

In `src/finab/tui/app.py`:

(a) Add to `BINDINGS` (after the `("i", "accounts_toggle_ignore", ...)` line):

```python
        ("y", "reconcile", "Reconcile to YNAB"),
```

(b) Add the dispatch method (near `action_accounts_toggle_ignore`):

```python
    def action_reconcile(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_reconcile()
        elif self._merchants_screen_active():
            self.query_one(MerchantsScreen).action_reconcile()
```

(c) Add a scoping set and a `check_action` clause. Add the constant near the other `_*_ACTIONS` sets:

```python
    _RECONCILE_ACTIONS = {"reconcile"}
```

In `check_action`, add before the final `return True`:

```python
        if action in self._RECONCILE_ACTIONS:
            return self._active_screen in ("screen-accounts", "screen-merchants")
```

- [ ] **Step 6: Write the failing app-scoping test**

Add to `tests/tui/test_app.py`:

```python
@pytest.mark.asyncio
async def test_check_action_reconcile_scoped_to_accounts_and_merchants():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._active_screen = "screen-accounts"
        await pilot.pause()
        assert app.check_action("reconcile", ()) is True
        app._active_screen = "screen-merchants"
        await pilot.pause()
        assert app.check_action("reconcile", ()) is True
        app._active_screen = "screen-sync"
        await pilot.pause()
        assert app.check_action("reconcile", ()) is False
```

- [ ] **Step 7: Run all affected suites**

Run: `uv run pytest tests/tui/test_accounts_screen.py tests/tui/test_merchants_screen.py tests/tui/test_app.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/finab/tui/app.py src/finab/tui/screens/accounts.py src/finab/tui/screens/merchants.py tests/tui/test_accounts_screen.py tests/tui/test_merchants_screen.py tests/tui/test_app.py
git commit -m "$(cat <<'EOF'
feat(tui): reconcile-to-YNAB action on Accounts/Merchants screens (A3)

Press `y` on either screen to recreate store entries missing on YNAB and
view a created/skipped/failed summary. Scoped via check_action.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: BudgetPickerModal widget

**Files:**
- Create: `src/finab/tui/widgets/budget_picker.py`
- Test: `tests/tui/test_budget_picker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_budget_picker.py`:

```python
import pytest


class _FakeBudget:
    """Matches the YNAB plan/budget shape — only id + name are read."""
    def __init__(self, id, name):
        self.id = id
        self.name = name


@pytest.mark.asyncio
async def test_budget_picker_dismisses_with_chosen_id():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                BudgetPickerModal(budgets=budgets),
                callback=lambda r: holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert holder["value"] in {"b-1", "b-2"}


@pytest.mark.asyncio
async def test_budget_picker_filters_by_name():
    from textual.app import App
    from textual.widgets import OptionList, Input
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]

    class _Host(App):
        def on_mount(self):
            self.push_screen(BudgetPickerModal(budgets=budgets))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one(Input).value = "bus"
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 1


@pytest.mark.asyncio
async def test_budget_picker_escape_returns_none():
    from textual.app import App
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                BudgetPickerModal(budgets=[_FakeBudget("b-1", "Personal")]),
                callback=lambda r: holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert holder["value"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tui/test_budget_picker.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the modal (mirrors YnabAccountPicker)**

Create `src/finab/tui/widgets/budget_picker.py`:

```python
"""BudgetPickerModal — fuzzy-search modal over the user's YNAB budgets.

Shown on first run when no valid budget is configured, and from the
Settings screen to switch budgets. Dismisses with the chosen budget id
(str), or None on cancel. Mirrors YnabAccountPicker.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class BudgetPickerModal(ModalScreen[Optional[str]]):
    """Returns the chosen budget id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, budgets: list, title: str = "Select a YNAB budget"):
        super().__init__()
        self._all = list(budgets)
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="budget-picker-dialog"):
            yield Static(self._title, id="budget-picker-title")
            yield Input(placeholder="filter…", id="budget-picker-filter")
            yield OptionList(id="budget-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#budget-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#budget-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [b for b in self._all if not f or f in str(b.name).lower()]
        for b in rows:
            ol.add_option(Option(str(b.name), id=str(b.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "budget-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#budget-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/tui/test_budget_picker.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/tui/widgets/budget_picker.py tests/tui/test_budget_picker.py
git commit -m "$(cat <<'EOF'
feat(tui): add BudgetPickerModal (first-run + switch budget)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: App bootstrap — budget resolution + load refactor (A1)

**Files:**
- Modify: `src/finab/tui/app.py` (`__init__`, `on_mount`, new `_load_and_bind` / `_bootstrap`, pure helpers)
- Test: `tests/tui/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `tests/tui/test_bootstrap.py`:

```python
import pytest


class _FakeBudget:
    def __init__(self, id, name):
        self.id = id
        self.name = name


def test_budget_is_valid_true_when_id_in_list():
    from finab.tui.app import FinabApp
    app = FinabApp()
    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    assert app._budget_is_valid("b-1", budgets) is True


def test_budget_is_valid_false_when_missing_or_stale():
    from finab.tui.app import FinabApp
    app = FinabApp()
    budgets = [_FakeBudget("b-1", "Personal")]
    assert app._budget_is_valid(None, budgets) is False
    assert app._budget_is_valid("b-9", budgets) is False


def test_auto_budget_returns_single_id_else_none():
    from finab.tui.app import FinabApp
    app = FinabApp()
    assert app._auto_budget([_FakeBudget("b-1", "Only")]) == "b-1"
    assert app._auto_budget([_FakeBudget("b-1", "A"), _FakeBudget("b-2", "B")]) is None
    assert app._auto_budget([]) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tui/test_bootstrap.py -v`
Expected: FAIL — `_budget_is_valid` / `_auto_budget` not defined.

- [ ] **Step 3: Add the pure helpers + `_budgets` field**

In `src/finab/tui/app.py`, in `__init__` (after `self.loaded = None`), add:

```python
        self._budgets: list = []
```

Add the two pure helpers (anywhere among the methods):

```python
    def _budget_is_valid(self, budget_id, budgets) -> bool:
        """True iff budget_id is non-None and present in the fetched budgets."""
        if budget_id is None:
            return False
        return any(str(getattr(b, "id", "")) == str(budget_id) for b in budgets)

    def _auto_budget(self, budgets) -> str | None:
        """Return the id when there's exactly one budget (auto-pick), else None."""
        if len(budgets) == 1:
            return str(getattr(budgets[0], "id", ""))
        return None
```

- [ ] **Step 4: Run to verify helper tests pass**

Run: `uv run pytest tests/tui/test_bootstrap.py -v`
Expected: PASS.

- [ ] **Step 5: Refactor `_kickoff_load` into `_load_and_bind` + add `_bootstrap`**

In `src/finab/tui/app.py`, extract the body of `_kickoff_load` (lines 170-205) into a plain coroutine, leave `_kickoff_load` as a thin worker, and add the bootstrap worker. Replace the existing `_kickoff_load` method with:

```python
    async def _load_and_bind(self) -> None:
        """Fetch all data for the current budget and (re)bind every screen.
        Shared by first-load, refresh, and switch-budget."""
        self.loaded = await load_all(
            fw_client=self._fw_client,
            ynab_client=self._ynab_client,
            budget_id=self._budget_id,
        )
        self._render_error_banner()
        if self.loaded.error is None and self._store and self._tx_store:
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.bind_data(
                loaded=self.loaded,
                store=self._store,
                tx_store=self._tx_store,
                ynab_payees=self.loaded.ynab_payees,
            )
            accounts_screen = self.query_one(AccountsScreen)
            accounts_screen.bind_data(
                store=self._store,
                fw_accounts=self.loaded.fw_accounts,
                ynab_accounts=self.loaded.ynab_accounts,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
            merchants_screen = self.query_one(MerchantsScreen)
            merchants_screen.bind_data(
                store=self._store,
                fw_transactions=self.loaded.fw_transactions,
                ynab_payees=self.loaded.ynab_payees,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
            memory_screen = self.query_one(MemoryScreen)
            memory_screen.bind_data(store=self._store)
        self._refresh_header_stats()

    @work(exclusive=True)
    async def _kickoff_load(self) -> None:
        await self._load_and_bind()

    @work(exclusive=True)
    async def _bootstrap(self) -> None:
        """Resolve the active budget, then load. Runs when both clients exist.

        - Fetch the budget list once (also cached for the Settings switcher).
        - If the stored budget_id is missing/stale: auto-pick when there's
          exactly one budget, else prompt with the picker.
        - Cancelling the picker with no valid budget leaves the error banner up.
        """
        import asyncio
        from finab.config import save_budget_id
        from finab.tui.widgets.budget_picker import BudgetPickerModal

        try:
            self._budgets = await asyncio.to_thread(self._ynab_client.get_budgets)
        except Exception as e:
            self.loaded = LoadedData(error=e)
            self._render_error_banner()
            return

        if not self._budget_is_valid(self._budget_id, self._budgets):
            chosen = self._auto_budget(self._budgets)
            if chosen is None:
                chosen = await self.push_screen_wait(
                    BudgetPickerModal(budgets=self._budgets)
                )
            if not chosen:
                self._show_banner("Select a budget to continue (Settings → b).")
                return
            save_budget_id(chosen)
            self._budget_id = chosen
            self.query_one(SettingsScreen).bind_data(budget_id=chosen)

        await self._load_and_bind()
```

Add the small banner helper (near `_render_error_banner`):

```python
    def _show_banner(self, message: str) -> None:
        try:
            self.query_one("#error-banner", ErrorBanner).show(message)
        except Exception:
            pass
```

- [ ] **Step 6: Point `on_mount` at `_bootstrap`**

In `on_mount` (`app.py:128`), replace:

```python
        if self._fw_client and self._ynab_client and self._budget_id:
            self._kickoff_load()
```

with:

```python
        if self._fw_client and self._ynab_client:
            self._bootstrap()
```

(Leave the `elif self._store is not None:` branch unchanged — it serves the store-only test scenarios.)

- [ ] **Step 7: Write the failing bootstrap integration tests**

Add to `tests/tui/test_bootstrap.py`:

```python
class _FakeFw:
    def get_accounts(self): return []
    def get_transactions(self, start_date=None, end_date=None): return []


class _FakeYnabClient:
    def __init__(self, budgets):
        self._budgets = budgets
    def get_budgets(self): return self._budgets
    def get_accounts(self, b): return []
    def get_transactions(self, b): return []
    def get_categories(self, b): return []
    def get_category_groups_with_categories(self, b): return []
    def get_payees(self, b): return []


def _make_app(monkeypatch, tmp_path, budgets, budget_id):
    """Build a FinabApp with fakes and a sandboxed config.json for save."""
    import finab.config as config_mod
    monkeypatch.setattr(config_mod, "CONFIG_FILE", tmp_path / "config.json")
    from finab.tui.app import FinabApp
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    return FinabApp(
        fw_client=_FakeFw(),
        ynab_client=_FakeYnabClient(budgets),
        budget_id=budget_id,
        store=ConfigStore(tmp_path / "config.json"),
        tx_store=TransactionsStore(tmp_path / "transactions.json"),
    )


@pytest.mark.asyncio
async def test_bootstrap_valid_budget_loads_without_picker(monkeypatch, tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal
    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id="b-1")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not isinstance(app.screen, BudgetPickerModal)
        assert app.loaded is not None and app.loaded.error is None


@pytest.mark.asyncio
async def test_bootstrap_single_budget_auto_selects(monkeypatch, tmp_path):
    from finab.config import load_budget_id
    budgets = [_FakeBudget("b-only", "Solo")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id=None)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app._budget_id == "b-only"
        assert load_budget_id() == "b-only"   # saved to sandboxed config


@pytest.mark.asyncio
async def test_bootstrap_missing_budget_multi_shows_picker(monkeypatch, tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal
    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause()
        assert isinstance(app.screen, BudgetPickerModal)
```

> Note: `await app.workers.wait_for_complete()` waits for the bootstrap/load workers to finish. If a test still races, add another `await pilot.pause()`. The picker test deliberately does *not* wait for completion (the worker is parked on `push_screen_wait`).

- [ ] **Step 8: Run the bootstrap suite + the existing app suite**

Run: `uv run pytest tests/tui/test_bootstrap.py tests/tui/test_app.py -v`
Expected: PASS. (Existing app tests use a clientless `FinabApp`, so `on_mount` takes neither branch — unaffected.)

- [ ] **Step 9: Commit**

```bash
git add src/finab/tui/app.py tests/tui/test_bootstrap.py
git commit -m "$(cat <<'EOF'
feat(tui): first-run budget resolution on boot (A1)

Fetch budgets on boot, validate the stored budget_id, auto-pick when
there's one budget else prompt with BudgetPickerModal, then load. Splits
_kickoff_load into a reusable _load_and_bind coroutine.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Manual refresh with flush-or-discard guard (A2)

**Files:**
- Modify: `src/finab/tui/app.py` (binding, `action_refresh`, `_on_refresh_confirm`, `check_action`)
- Test: `tests/tui/test_bootstrap.py` (refresh behavior)

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_bootstrap.py`:

```python
@pytest.mark.asyncio
async def test_refresh_with_no_pending_reloads_directly(monkeypatch, tmp_path):
    budgets = [_FakeBudget("b-1", "Personal")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id="b-1")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        calls = {"n": 0}
        monkeypatch.setattr(app, "_kickoff_load", lambda: calls.__setitem__("n", calls["n"] + 1))
        app.action_refresh()
        assert calls["n"] == 1


@pytest.mark.asyncio
async def test_refresh_with_pending_opens_guard(monkeypatch, tmp_path):
    from finab.tui.widgets.flush_confirm import FlushConfirmModal
    budgets = [_FakeBudget("b-1", "Personal")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id="b-1")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        monkeypatch.setattr(app, "_pending_count", lambda: 3)
        app.action_refresh()
        await pilot.pause()
        assert isinstance(app.screen, FlushConfirmModal)


def test_on_refresh_confirm_branches(monkeypatch, tmp_path):
    app = _make_app(monkeypatch, tmp_path, [_FakeBudget("b-1", "P")], budget_id="b-1")
    reloaded = {"n": 0}
    flushed = {"n": 0}
    monkeypatch.setattr(app, "_kickoff_load", lambda: reloaded.__setitem__("n", reloaded["n"] + 1))

    class _FakeSync:
        def action_flush(self): flushed["n"] += 1
    monkeypatch.setattr(app, "query_one", lambda *a, **k: _FakeSync())

    app._on_refresh_confirm("cancel")
    assert reloaded["n"] == 0 and flushed["n"] == 0

    app._on_refresh_confirm("skip")
    assert reloaded["n"] == 1 and flushed["n"] == 0

    app._on_refresh_confirm("flush")
    assert reloaded["n"] == 2 and flushed["n"] == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tui/test_bootstrap.py -k refresh -v`
Expected: FAIL — `action_refresh` / `_on_refresh_confirm` not defined.

- [ ] **Step 3: Add the binding**

In `src/finab/tui/app.py`, add to `BINDINGS` (after the `("question_mark", "show_help", ...)` line):

```python
        ("ctrl+r", "refresh", "Refresh"),
```

- [ ] **Step 4: Add `action_refresh` + `_on_refresh_confirm`**

Add to `FinabApp` (near `action_quit_with_confirm`):

```python
    def action_refresh(self) -> None:
        """Re-fetch all data and rebuild the engine. If there are unflushed
        decisions, guard first with the same flush-or-discard prompt as quit."""
        if not (self._fw_client and self._ynab_client and self._budget_id):
            return
        pending = self._pending_count()
        if pending == 0:
            self._kickoff_load()
            return
        from finab.tui.widgets.flush_confirm import FlushConfirmModal
        self.push_screen(
            FlushConfirmModal(pending_count=pending),
            callback=self._on_refresh_confirm,
        )

    def _on_refresh_confirm(self, result) -> None:
        if result == "cancel":
            return
        if result == "flush":
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_flush()
        # flush or skip -> reload
        self._kickoff_load()
```

- [ ] **Step 5: Make `refresh` always visible in the Footer**

In `check_action`, add `"refresh"` to the `_ALWAYS_VISIBLE` set:

```python
    _ALWAYS_VISIBLE = {"quit_with_confirm", "show_help", "refresh"}
```

- [ ] **Step 6: Run the refresh tests + app suite**

Run: `uv run pytest tests/tui/test_bootstrap.py tests/tui/test_app.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/app.py tests/tui/test_bootstrap.py
git commit -m "$(cat <<'EOF'
feat(tui): manual refresh (ctrl+r) with flush-or-discard guard (A2)

Re-fetch and rebuild via _kickoff_load. When unflushed decisions exist,
reuse FlushConfirmModal (flush/skip/cancel) before reloading.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Settings budget-switching (B1)

**Files:**
- Modify: `src/finab/tui/app.py` (binding, `action_switch_budget`, `_on_switch_budget`, `_settings_screen_active`, `check_action`)
- Test: `tests/tui/test_bootstrap.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_bootstrap.py`:

```python
@pytest.mark.asyncio
async def test_switch_budget_opens_picker_on_settings(monkeypatch, tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal
    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    app = _make_app(monkeypatch, tmp_path, budgets, budget_id="b-1")
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app._active_screen = "screen-settings"
        app.query_one("#content-switcher").current = "screen-settings"
        await pilot.pause()
        app.action_switch_budget()
        await pilot.pause()
        assert isinstance(app.screen, BudgetPickerModal)


def test_on_switch_budget_saves_and_reloads(monkeypatch, tmp_path):
    from finab.config import load_budget_id
    app = _make_app(monkeypatch, tmp_path, [_FakeBudget("b-1", "P"), _FakeBudget("b-2", "B")], budget_id="b-1")
    reloaded = {"n": 0}
    monkeypatch.setattr(app, "_kickoff_load", lambda: reloaded.__setitem__("n", reloaded["n"] + 1))

    class _FakeSettings:
        def bind_data(self, **k): pass
    monkeypatch.setattr(app, "query_one", lambda *a, **k: _FakeSettings())

    app._on_switch_budget(None)          # cancelled
    assert reloaded["n"] == 0
    app._on_switch_budget("b-2")         # chosen
    assert app._budget_id == "b-2"
    assert load_budget_id() == "b-2"
    assert reloaded["n"] == 1


@pytest.mark.asyncio
async def test_check_action_switch_budget_scoped_to_settings():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._active_screen = "screen-settings"
        await pilot.pause()
        assert app.check_action("switch_budget", ()) is True
        app._active_screen = "screen-sync"
        await pilot.pause()
        assert app.check_action("switch_budget", ()) is False
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/tui/test_bootstrap.py -k switch -v`
Expected: FAIL — `action_switch_budget` / `_on_switch_budget` / `switch_budget` scoping not defined.

- [ ] **Step 3: Add the binding + settings-active helper**

In `src/finab/tui/app.py`, add to `BINDINGS` (after the reconcile binding):

```python
        ("b", "switch_budget", "Switch budget"),
```

Add the helper (near `_memory_screen_active`):

```python
    def _settings_screen_active(self) -> bool:
        from textual.widgets import ContentSwitcher
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-settings"
```

- [ ] **Step 4: Add the action + callback**

Add to `FinabApp`:

```python
    def action_switch_budget(self) -> None:
        """From Settings, reopen the budget picker (using the cached list)."""
        if not self._settings_screen_active():
            return
        if not self._budgets:
            self.bell()
            return
        from finab.tui.widgets.budget_picker import BudgetPickerModal
        self.push_screen(
            BudgetPickerModal(budgets=self._budgets, title="Switch budget"),
            callback=self._on_switch_budget,
        )

    def _on_switch_budget(self, chosen) -> None:
        if not chosen or str(chosen) == str(self._budget_id):
            return
        from finab.config import save_budget_id
        save_budget_id(chosen)
        self._budget_id = chosen
        self.query_one(SettingsScreen).bind_data(budget_id=chosen)
        self._kickoff_load()
```

- [ ] **Step 5: Scope `switch_budget` to the Settings screen**

Add the constant near the other `_*_ACTIONS` sets:

```python
    _SETTINGS_ACTIONS = {"switch_budget"}
```

In `check_action`, add before the final `return True`:

```python
        if action in self._SETTINGS_ACTIONS:
            return self._active_screen == "screen-settings"
```

- [ ] **Step 6: Run the switch suite + full app suite**

Run: `uv run pytest tests/tui/test_bootstrap.py tests/tui/test_app.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/app.py tests/tui/test_bootstrap.py
git commit -m "$(cat <<'EOF'
feat(tui): switch budget from Settings (B1)

Press `b` on the Settings screen to reopen the budget picker from the
cached budget list; saving re-loads under the new budget.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Full suite + docs + help overlay

**Files:**
- Modify: `src/finab/tui/widgets/help_overlay.py` (document new keys), `README.md` (budget picker + refresh + reconcile), `CLAUDE.md` (key list, lifecycle)
- Test: whole suite

- [ ] **Step 1: Run the entire test suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions across engine + tui).

- [ ] **Step 2: Update the help overlay**

Read `src/finab/tui/widgets/help_overlay.py` and add the three new keys to the documented bindings: `ctrl+r — Refresh data`, `y — Reconcile to YNAB (Accounts/Merchants)`, `b — Switch budget (Settings)`. Match the file's existing formatting.

- [ ] **Step 3: Update README + CLAUDE.md**

In `README.md`: note that the first run now shows an in-app budget picker (no manual `config.json` editing), that `ctrl+r` refreshes, and that `y` reconciles accounts/merchants to YNAB. In `CLAUDE.md`: add `ctrl+r`, `y`, `b` to the relevant key lists and note that boot resolves the budget via `_bootstrap`.

- [ ] **Step 4: Commit**

```bash
git add src/finab/tui/widgets/help_overlay.py README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
docs(tui): document budget picker, refresh, and reconcile keys

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Finish the branch**

Use the `superpowers:finishing-a-development-branch` skill to decide how to integrate `lifecycle-enablers` (merge / PR / cleanup).

---

## Self-review

**Spec coverage:**
- A1 (first-run picker) → Task 6 (modal) + Task 7 (bootstrap resolution).
- A2 (refresh + guard) → Task 8.
- A3 (reconcile from TUI) → Task 3 (F1 result) + Task 4 (modal) + Task 5 (wiring).
- B1 (Settings switching) → Task 9.
- F1 (structured reconcile result) → Task 3.
- F2 (parallel load_all) → Task 2.
- F3 (dead placeholder) → Task 1.
- Testing requirements (picker, bootstrap, refresh, reconcile, load_all) → covered in Tasks 2,3,5,6,7,8,9.
- Risk "FlushConfirmModal needs a third action" → void; it already returns flush/skip/cancel (reused in Task 8).
- Decision "always fetch budgets on boot" → Task 7 `_bootstrap`. "Guard reuses flush prompt" → Task 8. "Reconcile manual per screen, no pre-confirm" → Task 5. "ctrl+r / y / b keys" → Tasks 8/5/9.

**Type consistency:** `ReconcileResult` (Task 3) is consumed unchanged by `ReconcileResultModal` (Task 4) and the screen actions (Task 5). `_budget_is_valid`/`_auto_budget`/`_load_and_bind`/`_bootstrap`/`_kickoff_load`/`action_refresh`/`_on_refresh_confirm`/`action_switch_budget`/`_on_switch_budget` names are used consistently across Tasks 7–9. `BudgetPickerModal(budgets=...)` signature matches every call site.

**Placeholder scan:** No TBD/TODO/"handle edge cases" left; every code step shows complete code. The only deferral (help-overlay exact formatting) is gated behind a Read in Task 10 Step 2 because that file wasn't quoted here.
