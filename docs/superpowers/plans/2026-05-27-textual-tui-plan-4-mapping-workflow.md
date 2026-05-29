# Textual TUI — Plan 4: First-Class Mapping Workflow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the mapping flow that the cutover deleted. The Accounts and Merchants screens currently only browse already-mapped store entries; this plan makes them surface unmapped FinWise accounts and merchants, and adds an `l` action that walks the user through mapping each one (alias → existing YNAB target, or create new). Folds in a bug fix for Plan 3 Task 8's Enter-binding regression.

**Architecture:** Pipe `LoadedData`, `ynab_client`, and `budget_id` through `AccountsScreen.bind_data` / `MerchantsScreen.bind_data` so the screens can detect "unmapped" entities and create new YNAB ones on demand. Two new modal pickers (`YnabAccountPicker`, `YnabPayeePicker`) fuzzy-search over the *fetched* YNAB data, not the store. Mapping is a chain of modals: alias input → (existing YNAB target via picker | create new). Engine helpers from Plan 1 (`_link_account_transfer_payee`, `_record_merchant_alias`) and YNAB client methods (`create_account`, `create_payee`) do the actual linking — Plan 4 just wires the UI to them.

**Tech Stack:** Python 3.14, uv, Textual 8.2.7 (existing), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-textual-tui-design.md` (§Accounts screen, §Merchants screen — the unmapped-row rendering and action keys that Plan 3 deferred).

**Scope boundary:** Plan 4 fixes the user-facing gap: unmapped FW accounts and merchants now have a path to being mapped from inside the TUI. Out of scope: budget switching from Settings, `.env` reload, the `_reconcile_*` print() cleanup, and the split editor's command-line UX (still deferred to a later polish pass).

---

## Lessons from Plans 1–3 (apply to all tasks)

1. **Textual 8.x renames:** `Static.content` (not `.renderable`); `ContentSwitcher` lives in `textual.widgets`.
2. **ListView consumes single-character keys** for type-to-search. App-level `BINDINGS` with screen-active guards (`_accounts_screen_active`, etc.) is the working pattern.
3. **ListView.clear()+append in tight succession races with Textual's deferred DOM removal.** Drop explicit IDs on `ListItem` and don't rebuild during the same event loop tick that just cleared.
4. **`OptionList.highlighted` defaults to None** — set explicitly to 0 after populating to enable Enter selection.
5. **`priority=True` on App bindings overrides modal Input.Submitted handlers.** Plan 3 Task 3 used it for the Sync `Enter` binding; Plan 4 Task 1 reverts that and routes Enter through `ListView.Selected` instead, so modal Inputs (CategoryPicker, AliasInput, etc.) get their Enter back.
6. **Don't shadow Textual framework names.** `Widget._render()` is real and gets called by the framework — don't define a private `_render` helper.
7. **Local imports inside `if` blocks** shadow module-top names for the whole function scope (Python scoping rule). Use module-top imports where possible.
8. **Amounts are milliunits** throughout.

---

## File Structure

**Created in this plan:**

```
src/finab/tui/widgets/
  ynab_account_picker.py    — fuzzy search over fetched YNAB accounts
  ynab_payee_picker.py      — fuzzy search over fetched YNAB payees
  yes_no_modal.py           — small confirm dialog (used for "create new YNAB account/payee?")
tests/tui/
  test_ynab_account_picker.py
  test_ynab_payee_picker.py
  test_yes_no_modal.py
```

**Modified in this plan:**

- `src/finab/tui/screens/accounts.py` — `bind_data` accepts fetched data + clients; renders unmapped rows; `action_link` implements the mapping flow.
- `src/finab/tui/screens/merchants.py` — same shape, but uses `fw_transactions` to derive distinct merchant_ids, and the link flow accounts for transfer-payee linking.
- `src/finab/tui/app.py` — `_kickoff_load` and `on_mount` pass the extra arguments to `bind_data`. The `Enter` binding loses its `priority=True`.
- `src/finab/tui/screens/sync.py` — add `on_list_view_selected` to handle Enter via the `ListView.Selected` message (replaces the priority-binding path).
- `tests/tui/test_accounts_screen.py` and `test_merchants_screen.py` — new tests for unmapped rendering + the link flow.
- `tests/tui/test_sync_screen.py` — `test_pressing_enter_applies_closest_history` may need its focus setup adjusted (PendingList needs focus for ListView.Selected to fire).

**Untouched:** Everything in `engine/`, `store.py`, `transactions.py`, `client.py`, `ynab_client.py`, `models.py`, `config.py`. Plan 4 is purely TUI plumbing on top of the engine surface from Plans 1–3.

---

## Task 1: Fix the Enter binding bug + add YnabAccountPicker

Replace Plan 3's `priority=True` Enter binding with a `ListView.Selected` handler on SyncScreen. This restores modal-Input Enter behavior. Also add `YnabAccountPicker` — the first of two new pickers we'll need for the mapping flow.

**Files:**
- Modify: `src/finab/tui/app.py` (remove priority from Enter binding)
- Modify: `src/finab/tui/screens/sync.py` (add Selected handler)
- Create: `src/finab/tui/widgets/ynab_account_picker.py`
- Modify: `tests/tui/test_sync_screen.py` (update the Enter test)
- Create: `tests/tui/test_ynab_account_picker.py`

### Step 1: Inspect the current Enter binding

Run: `grep -n "enter\|Binding" src/finab/tui/app.py | head -20`
Expected: the BINDINGS list includes an `enter` binding with `priority=True`.

### Step 2: Write the failing test for YnabAccountPicker

Create `tests/tui/test_ynab_account_picker.py`:

```python
"""Tests for YnabAccountPicker — a modal over the *fetched* YNAB accounts.

Different from AccountLinkPicker (which scans the store). This picker is
used during the mapping flow when the user wants to link a new FW account
to an existing YNAB account.
"""
import pytest


class _FakeYnabAccount:
    """Stub matching the YNAB SDK Account shape — only fields the picker reads."""
    def __init__(self, id, name, type="checking", deleted=False, closed=False):
        self.id = id
        self.name = name
        self.type = type
        self.deleted = deleted
        self.closed = closed


@pytest.mark.asyncio
async def test_picker_dismisses_with_chosen_account():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    accounts = [
        _FakeYnabAccount("yn-a", "Chase Checking"),
        _FakeYnabAccount("yn-b", "Emergency Fund", type="savings"),
    ]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabAccountPicker(ynab_accounts=accounts, title="Pick a YNAB account"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    # First account ("Chase Checking") — picker preserves the input order
    # but the test doesn't depend on alphabetical sort.
    assert result_holder["value"] in {"yn-a", "yn-b"}


@pytest.mark.asyncio
async def test_picker_filters_out_deleted_and_closed():
    """Deleted/closed YNAB accounts are not selectable."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    accounts = [
        _FakeYnabAccount("yn-a", "Visible"),
        _FakeYnabAccount("yn-b", "Deleted", deleted=True),
        _FakeYnabAccount("yn-c", "Closed", closed=True),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(YnabAccountPicker(ynab_accounts=accounts, title="Pick"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        # Only one option (Visible) should be available.
        assert ol.option_count == 1


@pytest.mark.asyncio
async def test_picker_escape_dismisses_with_none():
    from textual.app import App
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabAccountPicker(ynab_accounts=[_FakeYnabAccount("yn-a", "A")], title="Pick"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
```

### Step 3: Run the new tests to verify they fail

Run: `uv run pytest tests/tui/test_ynab_account_picker.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 4: Implement YnabAccountPicker

Write `src/finab/tui/widgets/ynab_account_picker.py`:

```python
"""YnabAccountPicker — fuzzy-search modal over *fetched* YNAB accounts.

Used during the Accounts screen's mapping flow when the user wants to
link a new FW account to an existing YNAB account (rather than create
a new one).

Dismisses with the chosen YNAB account's id (str), or None on cancel.

Different from AccountLinkPicker (which scans the store's already-mapped
accounts). This picker takes the raw YNAB-side list fetched at boot.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class YnabAccountPicker(ModalScreen[Optional[str]]):
    """Returns the chosen YNAB account's id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        ynab_accounts: list,
        title: str = "Pick a YNAB account",
    ):
        super().__init__()
        # Filter out deleted/closed accounts up front.
        self._all = [
            a for a in ynab_accounts
            if not getattr(a, "deleted", False)
            and not getattr(a, "closed", False)
        ]
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="ynab-account-picker-dialog"):
            yield Static(self._title, id="ynab-account-picker-title")
            yield Input(placeholder="filter…", id="ynab-account-picker-filter")
            yield OptionList(id="ynab-account-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#ynab-account-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#ynab-account-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [a for a in self._all if not f or f in str(a.name).lower()]
        for a in rows:
            label = f"{a.name}  ({a.type})"
            ol.add_option(Option(label, id=str(a.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ynab-account-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter inside the input picks the highlighted row."""
        ol = self.query_one("#ynab-account-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
```

Add styling to `src/finab/tui/styles.tcss`:

```tcss
YnabAccountPicker {
    align: center middle;
}

#ynab-account-picker-dialog {
    width: 60%;
    height: 60%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#ynab-account-picker-title {
    text-style: bold;
    padding-bottom: 1;
}
```

### Step 5: Run the picker tests

Run: `uv run pytest tests/tui/test_ynab_account_picker.py -v`
Expected: PASS — all 3 tests.

### Step 6: Fix the Enter binding regression

Edit `src/finab/tui/app.py`. Find the `BINDINGS` list. The Enter binding currently looks like one of:

```python
        Binding("enter", "sync_repeat_closest", "Repeat closest", priority=True),
```

or possibly:

```python
        ("enter", "sync_repeat_closest", "Repeat closest"),
```

If it's the `Binding(...)` form with `priority=True`, change it to the tuple form so the priority defaults to False:

```python
        ("enter", "sync_repeat_closest", "Repeat closest"),
```

The action method `action_sync_repeat_closest` stays. We're not removing it — we just stop priority-overriding ListView.

If the `from textual.binding import Binding` import becomes unused after this edit, remove it too. Check with: `grep "Binding" src/finab/tui/app.py`.

### Step 7: Add the Selected message handler on SyncScreen

Edit `src/finab/tui/screens/sync.py`. The class already has `on_list_view_highlighted` that updates the detail card on cursor moves. Add a sibling handler for `ListView.Selected` that triggers repeat-closest when the user presses Enter on the pending list:

```python
    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """ListView fires Selected on Enter. For the pending list, treat that
        as 'repeat closest history entry' — same behavior the App's `enter`
        binding triggers, but now routed through the message bus so it doesn't
        clash with modal Input.Submitted handlers."""
        pl = self.query_one("#sync-pending", PendingList)
        if event.list_view is not pl:
            return
        self.action_repeat_closest()
```

The App-level binding still works for users who press Enter when the sidebar (not the pending list) has focus. The Selected handler covers the common case of Enter-on-a-row.

### Step 8: Update the existing Enter test on SyncScreen

The `test_pressing_enter_applies_closest_history` test in `tests/tui/test_sync_screen.py` already presses Enter — it should still pass. But if the test was relying on the priority binding (e.g., pressing Enter without focusing the PendingList first), it may need adjustment. Run it and see:

Run: `uv run pytest tests/tui/test_sync_screen.py::test_pressing_enter_applies_closest_history -v`

If it fails, the fix is to ensure the PendingList has focus before pressing Enter. Add this to the test, just before the `await pilot.press("enter")` line:

```python
        pl.focus()
        await pilot.pause()
```

Re-run; should pass.

### Step 9: Run the full suite

Run: `uv run pytest`
Expected: 152 passing + 3 new picker tests = 155 passing.

### Step 10: Commit

```bash
git add src/finab/tui/widgets/ynab_account_picker.py src/finab/tui/app.py src/finab/tui/screens/sync.py src/finab/tui/styles.tcss tests/tui/test_ynab_account_picker.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): YnabAccountPicker + fix Enter binding regression via ListView.Selected"
```

---

## Task 2: YnabPayeePicker

Symmetric to Task 1 but for YNAB payees. Used by the Merchants mapping flow.

**Files:**
- Create: `src/finab/tui/widgets/ynab_payee_picker.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_ynab_payee_picker.py`

### Step 1: Write the failing tests

Create `tests/tui/test_ynab_payee_picker.py`:

```python
"""Tests for YnabPayeePicker — fuzzy search over fetched YNAB payees."""
import pytest


class _FakePayee:
    def __init__(self, id, name, deleted=False, transfer_account_id=None):
        self.id = id
        self.name = name
        self.deleted = deleted
        self.transfer_account_id = transfer_account_id


@pytest.mark.asyncio
async def test_picker_dismisses_with_payee_id():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    payees = [_FakePayee("yn-pa", "Costco"), _FakePayee("yn-pb", "Amazon")]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabPayeePicker(ynab_payees=payees, title="Pick payee"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder["value"] in {"yn-pa", "yn-pb"}


@pytest.mark.asyncio
async def test_picker_filters_out_transfer_payees_by_default():
    """Transfer payees (those with transfer_account_id set) are payees for
    YNAB's own accounts — they shouldn't be linkable as regular merchant payees.
    The picker hides them by default."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    payees = [
        _FakePayee("yn-pa", "Costco"),
        _FakePayee("yn-pb", "Transfer: Savings", transfer_account_id="yn-sav"),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(YnabPayeePicker(ynab_payees=payees, title="Pick"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 1


@pytest.mark.asyncio
async def test_picker_escape_dismisses_with_none():
    from textual.app import App
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabPayeePicker(ynab_payees=[_FakePayee("yn-pa", "A")], title="Pick"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_ynab_payee_picker.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement YnabPayeePicker

Write `src/finab/tui/widgets/ynab_payee_picker.py`:

```python
"""YnabPayeePicker — fuzzy-search modal over fetched YNAB payees.

Used during the Merchants screen's mapping flow when the user wants to
link a new FW merchant to an existing YNAB payee.

Filters out deleted payees and (by default) transfer payees, which are
internal YNAB constructs for own-account transfers — those don't make
sense as merchant linkages.

Dismisses with the chosen payee's id (str), or None on cancel.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class YnabPayeePicker(ModalScreen[Optional[str]]):
    """Returns the chosen payee's id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        ynab_payees: list,
        title: str = "Pick a YNAB payee",
    ):
        super().__init__()
        self._all = [
            p for p in ynab_payees
            if not getattr(p, "deleted", False)
            and not getattr(p, "transfer_account_id", None)
        ]
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="ynab-payee-picker-dialog"):
            yield Static(self._title, id="ynab-payee-picker-title")
            yield Input(placeholder="filter…", id="ynab-payee-picker-filter")
            yield OptionList(id="ynab-payee-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#ynab-payee-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#ynab-payee-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [p for p in self._all if not f or f in str(p.name).lower()]
        for p in rows:
            ol.add_option(Option(p.name, id=str(p.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ynab-payee-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#ynab-payee-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
```

Add styling to `src/finab/tui/styles.tcss`:

```tcss
YnabPayeePicker {
    align: center middle;
}

#ynab-payee-picker-dialog {
    width: 60%;
    height: 60%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#ynab-payee-picker-title {
    text-style: bold;
    padding-bottom: 1;
}
```

### Step 4: Run tests

Run: `uv run pytest tests/tui/test_ynab_payee_picker.py -v`
Expected: PASS — all 3 tests.

### Step 5: Run full suite

Run: `uv run pytest`
Expected: 158 passing.

### Step 6: Commit

```bash
git add src/finab/tui/widgets/ynab_payee_picker.py src/finab/tui/styles.tcss tests/tui/test_ynab_payee_picker.py
git commit -m "feat(tui): YnabPayeePicker for merchant mapping"
```

---

## Task 3: YesNoModal

A 2-button confirm dialog used by both mapping flows for the "no existing target — create new?" prompt.

**Files:**
- Create: `src/finab/tui/widgets/yes_no_modal.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_yes_no_modal.py`

### Step 1: Write the failing tests

Create `tests/tui/test_yes_no_modal.py`:

```python
"""Tests for YesNoModal — a 2-button yes/no confirm."""
import pytest


@pytest.mark.asyncio
async def test_yes_returns_true():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="Proceed?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert result["value"] is True


@pytest.mark.asyncio
async def test_no_returns_false():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
    assert result["value"] is False


@pytest.mark.asyncio
async def test_escape_returns_none():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result["value"] is None
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_yes_no_modal.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement YesNoModal

Write `src/finab/tui/widgets/yes_no_modal.py`:

```python
"""YesNoModal — minimal 2-button confirm.

Dismisses with True on `y`, False on `n`, None on Escape.

Used by the Accounts/Merchants mapping flow for the "no existing
target — create new?" prompt.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class YesNoModal(ModalScreen[Optional[bool]]):
    """Returns True / False / None (cancel)."""

    BINDINGS = [
        ("y", "dismiss(True)", "Yes"),
        ("n", "dismiss(False)", "No"),
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="yes-no-dialog"):
            yield Static(self._message, id="yes-no-message")
            yield Static("  y — Yes    n — No    Esc — Cancel", id="yes-no-hints")
```

Add styling:

```tcss
YesNoModal {
    align: center middle;
}

#yes-no-dialog {
    width: 60;
    height: auto;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#yes-no-message {
    padding-bottom: 1;
    text-style: bold;
}

#yes-no-hints {
    color: $text-muted;
}
```

### Step 4: Run tests

Run: `uv run pytest tests/tui/test_yes_no_modal.py -v`
Expected: PASS — all 3 tests.

### Step 5: Run full suite

Run: `uv run pytest`
Expected: 161 passing.

### Step 6: Commit

```bash
git add src/finab/tui/widgets/yes_no_modal.py src/finab/tui/styles.tcss tests/tui/test_yes_no_modal.py
git commit -m "feat(tui): YesNoModal for confirm prompts"
```

---

## Task 4: AccountsScreen — surface unmapped FW accounts

Extend `bind_data` to accept fetched FW + YNAB accounts and the YNAB client. Render unmapped FW accounts at the top of the list with `!` glyph. No mapping action yet — Task 5 wires the `l` flow.

**Files:**
- Modify: `src/finab/tui/screens/accounts.py`
- Modify: `src/finab/tui/app.py` (pass extra args to bind_data)
- Modify: `tests/tui/test_accounts_screen.py` (new test for unmapped rows)

### Step 1: Write the failing test

Append to `tests/tui/test_accounts_screen.py`:

```python
class _FakeFwAccount:
    """Stub matching the FinWise-side Account model fields the screen reads."""
    def __init__(self, finwise_id, name, type="checking"):
        self.finwise_id = finwise_id
        self.name = name
        self.type = type
        self.balance = 0
        self.currency_code = "USD"


@pytest.mark.asyncio
async def test_accounts_screen_shows_unmapped_fw_accounts(tmp_path):
    """When `fw_accounts` contains accounts not in the store, they
    render as unmapped rows with the `!` glyph."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)  # has fw-a (Chase) and fw-b (Crypto)
    fw_accounts = [
        _FakeFwAccount("fw-a", "Chase"),         # already in store
        _FakeFwAccount("fw-c", "BoA Card"),       # NEW — unmapped
    ]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(store=store, fw_accounts=fw_accounts, ynab_accounts=[], ynab_client=None, budget_id=None)
        await pilot.pause()
        # 2 mapped + 1 unmapped = 3 rows.
        assert ac.row_count() == 3
        # The unmapped row uses '!' glyph.
        assert ac.has_unmapped_for("fw-c")
```

### Step 2: Run test to verify it fails

Run: `uv run pytest tests/tui/test_accounts_screen.py::test_accounts_screen_shows_unmapped_fw_accounts -v`
Expected: FAIL — `bind_data` doesn't accept the new args.

### Step 3: Update AccountsScreen.bind_data + refresh_rows

Edit `src/finab/tui/screens/accounts.py`. The current `bind_data(store)` and `refresh_rows()` only consume the store. Extend them to track fetched data and render unmapped rows.

Update the class header so `__init__` stashes the extra state:

```python
class AccountsScreen(Container):
    """Sidebar entry #2 — browse and edit account mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None
        self._fw_accounts: list = []
        self._ynab_accounts: list = []
        self._ynab_client = None
        self._budget_id: Optional[str] = None
        # Row index → (kind, payload). kind in {"mapped", "unmapped"}.
        # payload: for "mapped", the store account dict; for "unmapped",
        # the FW account object.
        self._row_map: list = []
```

Replace `bind_data`:

```python
    def bind_data(
        self,
        *,
        store,
        fw_accounts: list = None,
        ynab_accounts: list = None,
        ynab_client=None,
        budget_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._fw_accounts = list(fw_accounts) if fw_accounts is not None else []
        self._ynab_accounts = list(ynab_accounts) if ynab_accounts is not None else []
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self.refresh_rows()
```

Replace `refresh_rows` so it renders unmapped first (visually distinct, action-needed), then mapped:

```python
    def refresh_rows(self) -> None:
        lv = self.query_one("#accounts-list", ListView)
        lv.clear()
        self._row_map = []
        if self._store is None:
            return

        # 1. Unmapped FW accounts — any fw_account whose finwise_id isn't
        # in the store yet.
        mapped_fw_ids = {
            (a.get("finwise") or {}).get("id")
            for a in self._store.accounts()
        }
        unmapped = [
            fw for fw in self._fw_accounts
            if getattr(fw, "finwise_id", None) and fw.finwise_id not in mapped_fw_ids
        ]
        for fw in unmapped:
            text = f"!  {fw.name:<22.22}  →  (unlinked — press `l` to map)"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("unmapped", fw))

        # 2. Mapped store accounts.
        for acc in self._store.accounts():
            glyph = _state_glyph(acc)
            alias = acc.get("alias", "?")
            ynab = acc.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            yn_type = ynab.get("type") or ""
            tag = " (tracking)" if yn_type in _TRACKING_TYPES else ""
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<22.22}  {yn_type}{tag}"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("mapped", acc))
```

Update `row_count` and add `has_unmapped_for`:

```python
    def row_count(self) -> int:
        return len(self._row_map)

    def has_unmapped_for(self, finwise_id: str) -> bool:
        """Test helper: did the unmapped row for this FW id render?"""
        for kind, payload in self._row_map:
            if kind == "unmapped" and getattr(payload, "finwise_id", None) == finwise_id:
                return True
        return False
```

Update `_current_account` so the existing `action_toggle_ignore` and `action_rename` keep working — only mapped rows are valid for those:

```python
    def _current_account(self) -> Optional[dict]:
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        kind, payload = self._row_map[idx]
        if kind == "mapped":
            return payload
        return None
```

Add a helper for unmapped rows that Task 5's `action_link` will need:

```python
    def _current_unmapped_fw(self):
        """Return the FW account stub at the current cursor, or None if
        the row is mapped (or there's no cursor)."""
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        kind, payload = self._row_map[idx]
        if kind == "unmapped":
            return payload
        return None
```

### Step 4: Update FinabApp._kickoff_load and on_mount to pass the extra args

Edit `src/finab/tui/app.py`. In `_kickoff_load`, update the AccountsScreen bind:

```python
            accounts_screen = self.query_one(AccountsScreen)
            accounts_screen.bind_data(
                store=self._store,
                fw_accounts=self.loaded.fw_accounts,
                ynab_accounts=self.loaded.ynab_accounts,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
```

In `on_mount` (the test-path branch where only a store is provided), the existing call passes only `store=` — that still works because the new args default to None/empty. No change needed in `on_mount`. Verify with a quick read.

### Step 5: Update the existing AccountsScreen tests

The two existing tests in `tests/tui/test_accounts_screen.py` (`test_accounts_screen_lists_accounts`, `test_accounts_screen_toggle_ignore`) construct `FinabApp(store=store)` and rely on `bind_data(store=store)` being called via on_mount. After this task, `bind_data` accepts more args but still works with just `store=`. Both tests should still pass.

If `set_cursor(0)` in `test_accounts_screen_toggle_ignore` now lands on an unmapped row (because the seed store had 2 accounts and they all render as mapped — wait, both are seeded so they're all mapped), nothing breaks. Cursor 0 stays on the first mapped row. Verify by re-running the existing tests after the changes.

### Step 6: Run all AccountsScreen tests

Run: `uv run pytest tests/tui/test_accounts_screen.py -v`
Expected: all 3 tests pass (2 existing + 1 new).

### Step 7: Run full suite

Run: `uv run pytest`
Expected: 162 passing.

### Step 8: Commit

```bash
git add src/finab/tui/screens/accounts.py src/finab/tui/app.py tests/tui/test_accounts_screen.py
git commit -m "feat(tui): AccountsScreen surfaces unmapped FW accounts"
```

---

## Task 5: AccountsScreen — `l` action implements the mapping flow

On an unmapped row, `l` opens a chain of modals: alias input → check if a YNAB account by that name exists → if yes, link it; if no, confirm-and-create. On a mapped row, `l` still bells (relink-to-different-YNAB is a separate workflow we're not building in Plan 4).

**Files:**
- Modify: `src/finab/tui/screens/accounts.py`
- Modify: `tests/tui/test_accounts_screen.py`

### Step 1: Write the failing test

Append to `tests/tui/test_accounts_screen.py`:

```python
class _FakeYnabAccount2:
    def __init__(self, id, name, type="checking"):
        self.id = id
        self.ynab_id = id
        self.name = name
        self.type = type
        self.balance = 0
        self.deleted = False
        self.closed = False
        self.transfer_payee_id = f"tp-{id}"


class _StubYnabClient:
    """Just enough of YNABClient for the mapping flow tests."""
    def __init__(self):
        self.created = []
    def create_account(self, budget_id, account):
        # The plan calls ynab_client.create_account(budget_id, Account_model).
        # Return an object with .data.account in the shape the engine helpers expect.
        new = _FakeYnabAccount2(id=f"yn-new-{len(self.created)}", name=account.name, type=account.type)
        self.created.append(new)

        class _Resp:
            class data:
                pass
        resp = _Resp()
        resp.data.account = new
        return resp


@pytest.mark.asyncio
async def test_link_unmapped_account_to_new_ynab(tmp_path):
    """When the user types an alias that doesn't match any existing YNAB
    account, the flow prompts 'create new?'; on yes, creates a YNAB
    account and adds the store entry."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")  # empty store

    fw_accounts = [_FakeFwAccount("fw-new", "BoA Card", type="creditCard")]
    ynab_accounts = []  # no existing YNAB accounts
    ynab_client = _StubYnabClient()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(
            store=store,
            fw_accounts=fw_accounts,
            ynab_accounts=ynab_accounts,
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        # Move cursor to the (only) unmapped row.
        ac.set_cursor(0)
        await pilot.pause()
        # Trigger the action programmatically so the test doesn't depend on
        # the `l` keybinding routing (which is tested via the app integration
        # path elsewhere).
        ac.action_link()
        await pilot.pause()
        # First modal is AliasInputModal — type the alias.
        from finab.tui.widgets.alias_input import AliasInputModal
        assert isinstance(app.screen, AliasInputModal)
        inp = app.screen.query_one("#alias-input-field")
        inp.value = "BoA Credit"
        await app.screen.action_dismiss_value(inp.value)  # falls back to action below

        # If the above doesn't work, dismiss programmatically:
        # (Different Textual versions differ on Input.Submitted plumbing.)
        if isinstance(app.screen, AliasInputModal):
            app.screen.dismiss("BoA Credit")
        await pilot.pause()
        # Since ynab_accounts is empty, no match → YesNoModal should appear.
        from finab.tui.widgets.yes_no_modal import YesNoModal
        assert isinstance(app.screen, YesNoModal)
        # Confirm create.
        await pilot.press("y")
        await pilot.pause()
        # ynab_client.create_account should have been called.
        assert len(ynab_client.created) == 1
        # Store now has the new account.
        assert store.account_by_finwise_id("fw-new") is not None
        assert store.account_by_finwise_id("fw-new")["alias"] == "BoA Credit"


@pytest.mark.asyncio
async def test_link_unmapped_account_to_existing_ynab(tmp_path):
    """If a YNAB account matches the alias by name, link to it without
    creating a new one."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from textual.widgets import ContentSwitcher

    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    fw_accounts = [_FakeFwAccount("fw-new", "BoA Card")]
    ynab_accounts = [_FakeYnabAccount2("yn-boa", "BoA Credit")]
    ynab_client = _StubYnabClient()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(
            store=store,
            fw_accounts=fw_accounts,
            ynab_accounts=ynab_accounts,
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        ac.set_cursor(0)
        ac.action_link()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        app.screen.dismiss("BoA Credit")
        await pilot.pause()
        # No YesNoModal should appear — we found a match.
        # The store should have the new account linked to yn-boa.
        assert ynab_client.created == []
        acc = store.account_by_finwise_id("fw-new")
        assert acc is not None
        assert acc["ynab"]["id"] == "yn-boa"
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_accounts_screen.py::test_link_unmapped_account_to_new_ynab tests/tui/test_accounts_screen.py::test_link_unmapped_account_to_existing_ynab -v`
Expected: FAIL — `action_link` currently just bells.

### Step 3: Implement action_link on AccountsScreen

Edit `src/finab/tui/screens/accounts.py`. Replace the existing `action_link` (currently `self.app.bell()`):

```python
    def action_link(self) -> None:
        """Map an unmapped FW account: prompt for alias, then either
        link to a YNAB account by name match or create a new one.

        On a mapped row, this is a no-op + bell (relink to a different
        YNAB account is a separate workflow not covered in Plan 4)."""
        fw = self._current_unmapped_fw()
        if fw is None:
            self.app.bell()
            return
        if self._ynab_client is None or self._budget_id is None:
            # Test paths without clients hit this; nothing to do.
            self.app.bell()
            return

        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Alias for '{fw.name}':",
            default=fw.name,
        )

        def _on_alias(alias):
            if alias is None:
                return
            self._continue_link_flow(fw, alias)

        self.app.push_screen(modal, callback=_on_alias)

    def _continue_link_flow(self, fw, alias: str) -> None:
        """After the alias is chosen, try to match against existing YNAB
        accounts; if no match, confirm-and-create."""
        from finab.store import normalize_alias
        match = next(
            (
                a for a in self._ynab_accounts
                if normalize_alias(getattr(a, "name", "")) == normalize_alias(alias)
                and not getattr(a, "deleted", False)
                and not getattr(a, "closed", False)
            ),
            None,
        )
        if match is not None:
            self._link_to_existing(fw, alias, match)
            return

        # No match — confirm create.
        from finab.tui.widgets.yes_no_modal import YesNoModal
        modal = YesNoModal(
            message=f"No YNAB account named '{alias}' exists. Create a new one?",
        )

        def _on_confirm(answer):
            if not answer:
                return
            self._create_and_link(fw, alias)

        self.app.push_screen(modal, callback=_on_confirm)

    def _link_to_existing(self, fw, alias: str, ynab_acc) -> None:
        """Link an unmapped FW account to an existing YNAB account."""
        fw_record = {
            "id": fw.finwise_id,
            "name": fw.name,
            "type": getattr(fw, "type", "checking"),
            "balance": getattr(fw, "balance", 0),
            "currency_code": getattr(fw, "currency_code", "USD"),
        }
        ynab_record = {
            "id": str(getattr(ynab_acc, "id", "") or getattr(ynab_acc, "ynab_id", "")),
            "name": ynab_acc.name,
            "type": getattr(ynab_acc, "type", "checking"),
            "balance": getattr(ynab_acc, "balance", 0),
            "transfer_payee_id": getattr(ynab_acc, "transfer_payee_id", None),
        }
        self._store.add_account(
            alias=alias,
            fw_record=fw_record,
            ynab_record=ynab_record,
            ignore_transactions=False,
        )
        self.refresh_rows()

    def _create_and_link(self, fw, alias: str) -> None:
        """Create a new YNAB account and link the FW account to it."""
        from finab.models import Account
        payload = Account(
            name=alias,
            type=getattr(fw, "type", None) or "checking",
            balance=getattr(fw, "balance", 0) or 0,
            currency_code=getattr(fw, "currency_code", "") or "",
        )
        try:
            response = self._ynab_client.create_account(self._budget_id, payload)
        except Exception:
            self.app.bell()
            return
        new_record = response.data.account
        new_id = str(getattr(new_record, "id", "") or getattr(new_record, "ynab_id", ""))
        ynab_record = {
            "id": new_id,
            "name": getattr(new_record, "name", alias),
            "type": getattr(new_record, "type", payload.type),
            "balance": getattr(new_record, "balance", payload.balance),
            "transfer_payee_id": (
                str(new_record.transfer_payee_id)
                if getattr(new_record, "transfer_payee_id", None) is not None
                else None
            ),
        }
        fw_record = {
            "id": fw.finwise_id,
            "name": fw.name,
            "type": getattr(fw, "type", "checking"),
            "balance": getattr(fw, "balance", 0),
            "currency_code": getattr(fw, "currency_code", "USD"),
        }
        self._store.add_account(
            alias=alias,
            fw_record=fw_record,
            ynab_record=ynab_record,
            ignore_transactions=False,
        )
        # Keep our local cache in sync so the next refresh sees the new account.
        self._ynab_accounts.append(new_record)
        self.refresh_rows()
```

Note: the test's `app.screen.dismiss(value)` call works because `ModalScreen` exposes `dismiss(value)` as a public method. The Input.Submitted plumbing inside `AliasInputModal` calls `self.dismiss(value)` for real users — programmatic dismissal in tests achieves the same effect.

### Step 4: Run the new tests

Run: `uv run pytest tests/tui/test_accounts_screen.py::test_link_unmapped_account_to_new_ynab tests/tui/test_accounts_screen.py::test_link_unmapped_account_to_existing_ynab -v`
Expected: PASS — both tests.

The first test calls `await app.screen.action_dismiss_value(inp.value)`. If that's not a real method on `AliasInputModal`, the test falls back to `app.screen.dismiss("BoA Credit")` — which is what actually works. Make sure that path triggers. (You may need to remove the `await app.screen.action_dismiss_value(...)` line entirely and replace with just the explicit `app.screen.dismiss("BoA Credit")` call. Adjust the test as needed.)

### Step 5: Run full suite

Run: `uv run pytest`
Expected: 164 passing.

### Step 6: Commit

```bash
git add src/finab/tui/screens/accounts.py tests/tui/test_accounts_screen.py
git commit -m "feat(tui): AccountsScreen action_link maps unmapped FW accounts"
```

---

## Task 6: MerchantsScreen — surface unmapped FW merchants + action_link

Same shape as Tasks 4+5 for accounts, but for merchants. Unmapped merchants are derived from `fw_transactions` (using the existing `_extract_distinct_merchants` helper in `engine/merchants.py`). The link flow checks three sources in priority order: (1) is the alias a store account → link to that account's transfer payee; (2) is there a YNAB payee by name → link; (3) confirm-and-create.

**Files:**
- Modify: `src/finab/tui/screens/merchants.py`
- Modify: `src/finab/tui/app.py`
- Modify: `tests/tui/test_merchants_screen.py`

### Step 1: Write the failing tests

Append to `tests/tui/test_merchants_screen.py`:

```python
class _FakeFwTxn:
    """Stub matching the Transaction shape that _extract_distinct_merchants reads."""
    def __init__(self, merchant_id, merchant_name=None, memo=None, amount=-1000):
        self.merchant_id = merchant_id
        self.merchant_name = merchant_name
        self.memo = memo
        self.original_description = None
        self.payee_name = None
        self.amount = amount
        from datetime import date as date_cls
        self.date = date_cls.today()


class _StubYnabClientForMerchants:
    def __init__(self):
        self.created_payees = []
    def create_payee(self, budget_id, name):
        class _P:
            pass
        p = _P()
        p.id = f"yn-new-payee-{len(self.created_payees)}"
        p.name = name
        p.transfer_account_id = None
        self.created_payees.append(p)
        return p


@pytest.mark.asyncio
async def test_merchants_screen_shows_unmapped(tmp_path):
    """Distinct merchant_ids from fw_transactions that aren't in the
    store render as unmapped rows."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)  # has fw-m1 (Costco) and fw-m2
    txns = [
        _FakeFwTxn(merchant_id="fw-m1"),   # mapped
        _FakeFwTxn(merchant_id="fw-new-x", merchant_name="New Merchant"),  # unmapped
    ]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        ms = app.query_one(MerchantsScreen)
        ms.bind_data(
            store=store,
            fw_transactions=txns,
            ynab_payees=[],
            ynab_client=None,
            budget_id=None,
        )
        await pilot.pause()
        # 2 mapped + 1 unmapped = 3 rows.
        assert ms.row_count() == 3
        assert ms.has_unmapped_for("fw-new-x")


@pytest.mark.asyncio
async def test_link_unmapped_merchant_to_new_payee(tmp_path):
    """Type an alias that doesn't match → confirm create → YNAB payee is created + linked."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from finab.tui.widgets.yes_no_modal import YesNoModal
    from textual.widgets import ContentSwitcher

    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")  # empty
    txns = [_FakeFwTxn(merchant_id="fw-merch-x", merchant_name="WeirdMart")]
    ynab_payees = []  # no existing payees
    ynab_client = _StubYnabClientForMerchants()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        ms = app.query_one(MerchantsScreen)
        ms.bind_data(
            store=store,
            fw_transactions=txns,
            ynab_payees=ynab_payees,
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        ms.set_cursor(0)  # the unmapped row is first
        ms.action_link()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        app.screen.dismiss("WeirdMart")
        await pilot.pause()
        assert isinstance(app.screen, YesNoModal)
        await pilot.press("y")
        await pilot.pause()
        assert len(ynab_client.created_payees) == 1
        m = store.merchant_by_alias("WeirdMart")
        assert m is not None
        assert m["finwise"]["id"] == "fw-merch-x"
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_merchants_screen.py::test_merchants_screen_shows_unmapped tests/tui/test_merchants_screen.py::test_link_unmapped_merchant_to_new_payee -v`
Expected: FAIL — bind_data signature mismatch + action_link bells.

### Step 3: Update MerchantsScreen

Edit `src/finab/tui/screens/merchants.py`. Expand the class similar to AccountsScreen Task 4:

```python
"""MerchantsScreen — sidebar entry #3.

Lists merchants with state glyph + alias + linked-to. Surfaces
unmapped merchants derived from fw_transactions and provides an
action_link flow to map them.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


def _merchant_glyph(m: dict) -> str:
    ynab = m.get("ynab") or {}
    if ynab.get("transfer_account_id"):
        return "→"
    if ynab.get("id"):
        return "✓"
    return "!"


class MerchantsScreen(Container):
    """Sidebar entry #3 — browse and edit merchant mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None
        self._fw_transactions: list = []
        self._ynab_payees: list = []
        self._ynab_client = None
        self._budget_id: Optional[str] = None
        # Row index → (kind, payload). kind in {"mapped", "unmapped"}.
        self._row_map: list = []

    def compose(self) -> ComposeResult:
        yield ListView(id="merchants-list")

    def bind_data(
        self,
        *,
        store,
        fw_transactions: list = None,
        ynab_payees: list = None,
        ynab_client=None,
        budget_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._fw_transactions = list(fw_transactions) if fw_transactions is not None else []
        self._ynab_payees = list(ynab_payees) if ynab_payees is not None else []
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self.refresh_rows()

    def refresh_rows(self) -> None:
        lv = self.query_one("#merchants-list", ListView)
        lv.clear()
        self._row_map = []
        if self._store is None:
            return

        # 1. Unmapped merchants — derive from fw_transactions.
        from finab.engine.merchants import _extract_distinct_merchants
        all_distinct = _extract_distinct_merchants(self._fw_transactions)
        mapped_fw_ids = {
            (m.get("finwise") or {}).get("id")
            for m in self._store.merchants()
        }
        unmapped = [d for d in all_distinct if d["id"] not in mapped_fw_ids]
        for fw_m in unmapped:
            name = fw_m.get("name") or "(no name)"
            text = f"!  {name:<22.22}  →  (unlinked — press `l` to map)"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("unmapped", fw_m))

        # 2. Mapped store merchants.
        for m in self._store.merchants():
            glyph = _merchant_glyph(m)
            alias = m.get("alias", "?")
            ynab = m.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            link_kind = "transfer payee" if ynab.get("transfer_account_id") else ("payee" if ynab.get("id") else "")
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<26.26}  {link_kind}"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("mapped", m))

    def row_count(self) -> int:
        return len(self._row_map)

    def has_unmapped_for(self, fw_id: str) -> bool:
        for kind, payload in self._row_map:
            if kind == "unmapped" and payload.get("id") == fw_id:
                return True
        return False

    def set_cursor(self, index: int) -> None:
        self.query_one("#merchants-list", ListView).index = index

    def _current_row(self):
        lv = self.query_one("#merchants-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        return self._row_map[idx]

    def _current_merchant(self) -> Optional[dict]:
        row = self._current_row()
        if row is None or row[0] != "mapped":
            return None
        return row[1]

    def _current_unmapped(self):
        row = self._current_row()
        if row is None or row[0] != "unmapped":
            return None
        return row[1]

    def action_rename(self) -> None:
        m = self._current_merchant()
        if m is None or self._store is None:
            return
        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Rename '{m['alias']}':",
            default=m.get("alias", ""),
        )

        def _on_done(new_alias):
            if new_alias is None or new_alias == m.get("alias"):
                return
            self._store.set_merchant_alias(m["id"], new_alias)
            self.refresh_rows()

        self.app.push_screen(modal, callback=_on_done)

    def action_link(self) -> None:
        """Map an unmapped merchant. Bells on a mapped row."""
        fw_m = self._current_unmapped()
        if fw_m is None:
            self.app.bell()
            return
        if self._ynab_client is None or self._budget_id is None:
            self.app.bell()
            return

        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Alias for merchant '{fw_m.get('name') or fw_m['id']}':",
            default=fw_m.get("name") or "",
        )

        def _on_alias(alias):
            if alias is None:
                return
            self._continue_link_flow(fw_m, alias)

        self.app.push_screen(modal, callback=_on_alias)

    def _continue_link_flow(self, fw_m: dict, alias: str) -> None:
        """Three-source resolution: store-account-as-transfer-payee,
        existing YNAB payee, or create new payee."""
        from finab.engine.merchants import _link_account_transfer_payee
        # 1. Does the alias match a store account? Link to that account's
        # transfer payee (own-account transfers).
        if _link_account_transfer_payee(self._store, self._ynab_payees, alias, fw_m):
            self.refresh_rows()
            return

        # 2. Existing YNAB payee by name?
        from finab.store import normalize_alias, to_dict
        match = next(
            (
                p for p in self._ynab_payees
                if normalize_alias(getattr(p, "name", "")) == normalize_alias(alias)
                and not getattr(p, "deleted", False)
                and not getattr(p, "transfer_account_id", None)
            ),
            None,
        )
        if match is not None:
            self._store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(match),
            )
            self.refresh_rows()
            return

        # 3. No match — confirm create.
        from finab.tui.widgets.yes_no_modal import YesNoModal
        modal = YesNoModal(
            message=f"No YNAB payee named '{alias}' exists. Create a new one?",
        )

        def _on_confirm(answer):
            if not answer:
                return
            self._create_and_link(fw_m, alias)

        self.app.push_screen(modal, callback=_on_confirm)

    def _create_and_link(self, fw_m: dict, alias: str) -> None:
        try:
            new_payee = self._ynab_client.create_payee(self._budget_id, alias)
        except Exception:
            self.app.bell()
            return
        from finab.store import to_dict
        self._store.add_merchant(
            alias=alias,
            fw_record=fw_m,
            ynab_record=to_dict(new_payee),
        )
        self._ynab_payees.append(new_payee)
        self.refresh_rows()
```

### Step 4: Update FinabApp to pass extra args to MerchantsScreen.bind_data

Edit `src/finab/tui/app.py`. In `_kickoff_load`, update:

```python
            merchants_screen = self.query_one(MerchantsScreen)
            merchants_screen.bind_data(
                store=self._store,
                fw_transactions=self.loaded.fw_transactions,
                ynab_payees=self.loaded.ynab_payees,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
```

The `on_mount` test-path bind (just `store=`) still works.

### Step 5: Run the new tests

Run: `uv run pytest tests/tui/test_merchants_screen.py -v`
Expected: PASS — including existing tests + 2 new.

### Step 6: Run the full suite

Run: `uv run pytest`
Expected: 166 passing.

### Step 7: Commit

```bash
git add src/finab/tui/screens/merchants.py src/finab/tui/app.py tests/tui/test_merchants_screen.py
git commit -m "feat(tui): MerchantsScreen surfaces unmapped + action_link maps to YNAB payee"
```

---

## Task 7: Final verification

End-to-end verification. No code changes.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -v 2>&1 | tail -40`
Expected: 166 passing total, zero failures.

- [ ] **Step 2: Engine boundary check (still pristine)**

Run: `grep -rn "input(" src/finab/engine/ || echo "OK no input()"`
Run: `grep -rn "^from textual\|^import textual" src/finab/engine/ || echo "OK no textual imports"`
Both should report no matches.

- [ ] **Step 3: Confirm the unmapped-render path works without clients**

The `on_mount` test-path branch passes only `store=` to each screen's `bind_data`. Verify the screens still render the mapped subset without errors when fw_accounts/fw_transactions are absent:

Run: `uv run pytest tests/tui/test_accounts_screen.py::test_accounts_screen_lists_accounts tests/tui/test_merchants_screen.py::test_merchants_screen_lists_merchants -v`
Expected: both pass.

- [ ] **Step 4: Smoke import check**

```bash
uv run python -c "from finab.tui.widgets.ynab_account_picker import YnabAccountPicker; print('OK')"
uv run python -c "from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker; print('OK')"
uv run python -c "from finab.tui.widgets.yes_no_modal import YesNoModal; print('OK')"
```

All should print `OK`.

- [ ] **Step 5: TUI launch (if a terminal is available)**

Run: `uv run finab`
Expected: TUI starts. With FW/YNAB credentials available, navigate to Accounts — any FW accounts not yet in the store should appear at the top with `!` glyph. Pressing `l` on an unmapped row should walk through the alias → match/create flow.

If no terminal, skip and note in the report.

- [ ] **Step 6: Commit log review**

Run: `git log --oneline HEAD~7..HEAD`
Expected: 6 implementation commits + verification (which has no commit). Report count.

## Self-Review

**Spec coverage:**
- Spec §Accounts screen "Single list of every FinWise account currently visible to FinWiseClient.get_accounts(). State icon shows where each one stands" — covered by Task 4.
- Spec §Accounts screen actions "rename / link to existing / create new YNAB account / toggle ignore" — `a` (rename) and `i` (toggle) are pre-existing from Plan 3; `l` (link / create) is covered by Task 5; the `n` "new" key from the spec is conflated into `l` (the alias-then-confirm-create flow handles both paths in one keypress).
- Spec §Merchants screen "every distinct FW merchant ever seen in transactions" — Task 6.
- Spec §Merchants screen action `l` (link to YNAB payee or to own account's transfer payee) — Task 6's `action_link` implements the three-source priority.
- Spec §Merchants screen action `u` (unlink) — out of scope for Plan 4; rare maintenance op.

**Placeholder scan:**
- No "TBD" / "implement later" in step bodies. The plan acknowledges deferred work (relink-to-different-YNAB, merchant unlink, budget switching) but doesn't pretend to ship them.

**Type consistency:**
- `AccountsScreen.bind_data(store, fw_accounts, ynab_accounts, ynab_client, budget_id)` — signature consistent across Tasks 4 and 5 and the FinabApp callsite.
- `MerchantsScreen.bind_data(store, fw_transactions, ynab_payees, ynab_client, budget_id)` — symmetric for Task 6.
- `YnabAccountPicker(ynab_accounts=, title=)` and `YnabPayeePicker(ynab_payees=, title=)` — distinct names matching the data they consume.
- `YesNoModal(message=)` returns `bool | None` — Task 3 impl + Task 5 / 6 callers agree.
- `_extract_distinct_merchants` lives in `engine/merchants.py` and is imported lazily inside `MerchantsScreen.refresh_rows` to avoid circular imports at module load. Same pattern as `_link_account_transfer_payee` usage.

**One known gap for a future plan:**
- The `action_link` flow doesn't expose the "ignore_transactions" choice that the old CLI prompted for after alias. The user can toggle with `i` after linking, but it's an extra step. Worth a small UX iteration later.

---
