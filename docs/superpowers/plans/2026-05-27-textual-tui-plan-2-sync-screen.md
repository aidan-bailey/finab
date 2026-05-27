# Textual TUI — Plan 2: App Scaffolding + Sync Screen

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Textual TUI on top of the Plan 1 engine: scaffold `src/finab/tui/`, build `FinabApp` with a sidebar of screens, and implement a fully functional **Sync screen** (master/detail layout with category picker, split editor, and repeat-from-history modals). Behind a `FINAB_TUI=1` env flag so the existing CLI keeps working untouched.

**Architecture:** New `src/finab/tui/` subpackage. `FinabApp` (subclass of `textual.app.App`) owns a sidebar (`ListView`) and a `ContentSwitcher` for screens. Async data loading via `@work` decorated worker. Sync screen consumes a `SyncEngine` instance and renders its candidate list as a master/detail layout. Three modal screens handle category picking (fuzzy search), splitting (live remainder validation), and repeat-from-history. Each modal `dismiss()`es with a typed result that the parent screen turns into an engine call (`apply_category`, `apply_split`, `apply_transfer`).

**Tech Stack:** Python 3.14, uv, `textual` (new dep), existing `finab.engine.sync.SyncEngine`. No `pytest-textual-snapshot` — tests use `app.run_test()` + `Pilot` directly.

**Spec:** `docs/superpowers/specs/2026-05-27-textual-tui-design.md` (migration plan steps 4–5, focused on Sync screen).

**Scope boundary:** This plan does NOT touch the existing CLI entrypoint. `uv run finab` keeps invoking the prompt-based flow; the TUI is opt-in via env flag. Accounts/Merchants/Memory/Settings screens are placeholder stubs in this plan — they get real implementations in Plan 3. Cutover (making the TUI the default) is also Plan 3.

---

## File Structure

**Created in this plan:**

```
src/finab/tui/
  __init__.py                 — package marker (docstring only)
  app.py                      — FinabApp(App) — root, owns sidebar + content switcher
  styles.tcss                 — global TCSS rules (sidebar width, modal sizing, glyph colours)
  data_loader.py              — async fetch coordinator (calls FinWiseClient + YNABClient)
  screens/
    __init__.py               — package marker
    sync.py                   — SyncScreen + KEY_BINDINGS for the screen
    placeholder.py            — PlaceholderScreen("Accounts" / "Merchants" / etc.)
  widgets/
    __init__.py               — package marker
    pending_list.py           — PendingList(ListView) — glyph + alias + amount per row
    transaction_card.py       — TransactionCard(Static) — date/amount/memo/status detail
    category_picker.py        — CategoryPickerModal(ModalScreen) — input + filtered OptionList
    split_editor.py           — SplitEditorModal(ModalScreen) — live amount/category/memo table
    history_picker.py         — HistoryPickerModal(ModalScreen) — OptionList over processings
tests/tui/
  __init__.py
  test_app.py                 — boot smoke test, sidebar navigation
  test_sync_screen.py         — pilot-driven interaction tests for SyncScreen
  test_category_picker.py     — modal dismiss-with-result tests
  test_split_editor.py        — modal dismiss-with-result + sum-invariant tests
```

**Modified in this plan:**

- `pyproject.toml` — add `textual>=4.0.0` to dependencies.
- `src/finab/main.py` — `main()` checks `FINAB_TUI` env var; when set, calls `FinabApp().run()` instead of the existing prompt flow. Existing logic stays in place behind the env-var check.

**Untouched:** Everything else. The engine subpackage from Plan 1 is consumed but not modified. The CLI prompt code in `transactions.py` and `main.py` is unchanged.

---

## Task 1: Add textual dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add textual to dependencies**

Edit `pyproject.toml`. In the `dependencies` array, add `"textual>=4.0.0"`:

```toml
dependencies = [
    "finwise-python>=1.4.0",
    "python-dotenv>=1.2.1",
    "textual>=4.0.0",
    "ynab>=4.1.0",
]
```

- [ ] **Step 2: Sync dependencies**

Run: `uv sync`
Expected: textual installs cleanly. Note the version installed in case the implementer needs to reference docs.

- [ ] **Step 3: Verify the existing test suite still passes**

Run: `uv run pytest`
Expected: 174 passing. Adding a dependency shouldn't break anything; if it does, stop and investigate before continuing.

- [ ] **Step 4: Verify textual can be imported**

Run: `uv run python -c "from textual.app import App; from textual.screen import Screen, ModalScreen; print('OK')"`
Expected: `OK`. Confirms the install works.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add textual for upcoming TUI"
```

---

## Task 2: Scaffold tui/ package + minimal FinabApp

Create the `src/finab/tui/` directory and a barest-possible `FinabApp` that boots, shows "Hello finab" text, and exits on `q`. No screens, no sidebar yet — just confirm Textual can launch.

**Files:**
- Create: `src/finab/tui/__init__.py`
- Create: `src/finab/tui/app.py`
- Create: `src/finab/tui/screens/__init__.py`
- Create: `src/finab/tui/widgets/__init__.py`
- Create: `tests/tui/__init__.py`
- Create: `tests/tui/test_app.py`

- [ ] **Step 1: Write the failing boot smoke test**

Create `tests/tui/test_app.py`:

```python
"""Smoke tests for FinabApp.

Each test uses Textual's Pilot to interact with the app headlessly —
no terminal required. The conftest sandbox (which re-points state
file paths) applies here too.
"""
import pytest


@pytest.mark.asyncio
async def test_app_boots_and_shows_hello():
    """The bare app starts and the welcome static is on screen."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        # Wait one frame for mount.
        await pilot.pause()
        # The hello widget should be there. We look for any Static with
        # the expected text; selector doesn't matter for smoke level.
        from textual.widgets import Static
        statics = app.query(Static)
        texts = [str(s.renderable) for s in statics]
        assert any("Hello finab" in t for t in texts), f"got: {texts}"


@pytest.mark.asyncio
async def test_app_exits_on_q():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        # After q, app should be exited. run_test context will complete.
    # If we got here without timing out, the app exited cleanly.
```

You will likely need `pytest-asyncio` to run async tests. Check whether it's already installed:

Run: `uv run python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

If the import fails, add it to `pyproject.toml` `dev` deps:

```toml
[dependency-groups]
dev = [
    "pytest>=9.0.2",
    "pytest-asyncio>=0.24.0",
]
```

Then `uv sync` and add `asyncio_mode = "auto"` to the pyproject's pytest config (if not already present):

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

If `[tool.pytest.ini_options]` doesn't exist yet in `pyproject.toml`, add it. Once `asyncio_mode = "auto"` is set, you can remove the `@pytest.mark.asyncio` markers (they're no-ops but harmless). Leave them for now.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.tui'`.

- [ ] **Step 3: Create the package markers**

Write `src/finab/tui/__init__.py`:

```python
"""Textual TUI for finab.

Subpackages:
  screens/  — full-screen views (Sync, Accounts, Merchants, Memory, Settings)
  widgets/  — reusable widgets and modal screens used across screens

This package may import from finab.engine, finab.store, finab.models,
finab.client, finab.ynab_client. The reverse is forbidden — engine and
data layers must remain Textual-free.
"""
```

Write `src/finab/tui/screens/__init__.py` and `src/finab/tui/widgets/__init__.py` as completely empty files (0 bytes).

Write `tests/tui/__init__.py` as a 0-byte file.

- [ ] **Step 4: Create the minimal FinabApp**

Write `src/finab/tui/app.py`:

```python
"""FinabApp — root Textual application.

Plan 2 boot: shows a 'Hello finab' static and exits on 'q'. Plan 2
later tasks layer on the sidebar, screens, and data loading.
"""
from textual.app import App, ComposeResult
from textual.widgets import Static


class FinabApp(App):
    """Root app. Owns the sidebar (left) and content area (right)."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Hello finab", id="hello")
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: PASS — both tests.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: 176 passing (174 prior + 2 new). No failures.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/__init__.py src/finab/tui/app.py src/finab/tui/screens/__init__.py src/finab/tui/widgets/__init__.py tests/tui/__init__.py tests/tui/test_app.py pyproject.toml uv.lock
git commit -m "feat(tui): scaffold tui package and minimal FinabApp"
```

---

## Task 3: FinabApp with sidebar + placeholder screens

Replace the "Hello finab" static with a sidebar `ListView` on the left and a `ContentSwitcher` on the right that swaps between screens. All screens are `PlaceholderScreen` instances showing the screen name. Sidebar items navigate.

**Files:**
- Modify: `src/finab/tui/app.py`
- Create: `src/finab/tui/screens/placeholder.py`
- Create: `src/finab/tui/styles.tcss`
- Modify: `tests/tui/test_app.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_app.py`:

```python
@pytest.mark.asyncio
async def test_sidebar_has_five_screens():
    """Sidebar lists Sync, Accounts, Merchants, Memory, Settings."""
    from finab.tui.app import FinabApp
    from textual.widgets import ListItem
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        items = app.query(ListItem)
        labels = [item.query_one("Label").renderable for item in items]
        labels_str = [str(l) for l in labels]
        assert labels_str == ["Sync", "Accounts", "Merchants", "Memory", "Settings"]


@pytest.mark.asyncio
async def test_sidebar_default_focus_is_sync():
    """The Sync placeholder content is visible by default."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher")
        assert switcher.current == "screen-sync"


@pytest.mark.asyncio
async def test_sidebar_navigation_switches_content():
    """Moving the sidebar cursor to 'Accounts' makes Accounts visible."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Focus the sidebar (default focus might be elsewhere — query and focus).
        sidebar = app.query_one("#sidebar")
        sidebar.focus()
        await pilot.pause()
        # Move cursor down by one to land on Accounts.
        await pilot.press("down")
        await pilot.pause()
        switcher = app.query_one("#content-switcher")
        assert switcher.current == "screen-accounts"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_app.py::test_sidebar_has_five_screens -v`
Expected: FAIL — sidebar doesn't exist yet.

- [ ] **Step 3: Create the placeholder screen**

Write `src/finab/tui/screens/placeholder.py`:

```python
"""Placeholder screen used by Plan 2 for non-Sync sidebar entries.

Plan 3 replaces these with real implementations. For Plan 2, all five
sidebar entries point at this — only Sync gets a real screen.
"""
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class PlaceholderScreen(Container):
    """A simple container that displays the screen name and a 'not yet'
    notice. Embedded inside the content switcher, not pushed as a Screen,
    so sidebar navigation feels instant.
    """

    def __init__(self, name: str, *, id: str = None):
        super().__init__(id=id)
        self._screen_name = name

    def compose(self) -> ComposeResult:
        yield Static(f"  {self._screen_name}\n\n  Not yet implemented (Plan 3).", classes="placeholder-body")
```

- [ ] **Step 4: Create the styles.tcss**

Write `src/finab/tui/styles.tcss`:

```tcss
Screen {
    layout: horizontal;
}

#sidebar {
    width: 18;
    border-right: thick $primary;
    background: $surface;
}

#sidebar > ListItem {
    padding: 0 1;
}

#sidebar > ListItem.--highlight {
    background: $primary;
    color: $background;
}

#content-switcher {
    width: 1fr;
}

.placeholder-body {
    padding: 2 4;
    color: $text-muted;
}
```

- [ ] **Step 5: Rewrite FinabApp to use sidebar + content switcher**

Overwrite `src/finab/tui/app.py`:

```python
"""FinabApp — root Textual application.

Layout: sidebar (left) + content switcher (right). Sidebar selection
changes the active content. Sync is the default.
"""
from textual.app import App, ComposeResult
from textual.containers import ContentSwitcher, Horizontal
from textual.widgets import Label, ListItem, ListView

from finab.tui.screens.placeholder import PlaceholderScreen


SCREEN_IDS = [
    ("Sync", "screen-sync"),
    ("Accounts", "screen-accounts"),
    ("Merchants", "screen-merchants"),
    ("Memory", "screen-memory"),
    ("Settings", "screen-settings"),
]


class FinabApp(App):
    """Root app: sidebar nav + content switcher."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(
                *[ListItem(Label(name), id=f"item-{sid}") for name, sid in SCREEN_IDS],
                id="sidebar",
            )
            with ContentSwitcher(initial="screen-sync", id="content-switcher"):
                for name, sid in SCREEN_IDS:
                    yield PlaceholderScreen(name, id=sid)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Highlight (cursor move) on the sidebar swaps the visible screen."""
        if event.item is None:
            return
        item_id = event.item.id  # e.g. "item-screen-accounts"
        if item_id and item_id.startswith("item-"):
            screen_id = item_id.removeprefix("item-")
            switcher = self.query_one("#content-switcher", ContentSwitcher)
            switcher.current = screen_id
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: all 5 tests pass (2 from Task 2 + 3 new).

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: 179 passing.

- [ ] **Step 8: Commit**

```bash
git add src/finab/tui/app.py src/finab/tui/screens/placeholder.py src/finab/tui/styles.tcss tests/tui/test_app.py
git commit -m "feat(tui): sidebar with five screens and content switcher"
```

---

## Task 4: Wire FINAB_TUI env flag to launch the TUI

Make `uv run finab` check the `FINAB_TUI` env var. When set (any truthy value), launch `FinabApp().run()`. When unset, fall through to the existing prompt-based `main` flow.

**Files:**
- Modify: `src/finab/main.py` (only the `main()` function)
- Modify: `tests/tui/test_app.py` (add an entrypoint integration test)

- [ ] **Step 1: Write the failing entrypoint test**

Add to `tests/tui/test_app.py`:

```python
def test_main_launches_tui_when_flag_set(monkeypatch):
    """When FINAB_TUI=1, finab.main.main() should construct and run FinabApp."""
    monkeypatch.setenv("FINAB_TUI", "1")

    launched = {"called": False}

    class FakeApp:
        def __init__(self): pass
        def run(self):
            launched["called"] = True

    import finab.tui.app as tui_app_mod
    monkeypatch.setattr(tui_app_mod, "FinabApp", FakeApp)

    from finab.main import main
    main()

    assert launched["called"] is True


def test_main_falls_through_to_cli_when_flag_unset(monkeypatch):
    """When FINAB_TUI is unset, finab.main.main() does NOT touch FinabApp."""
    monkeypatch.delenv("FINAB_TUI", raising=False)

    # We can't easily fake the entire CLI flow, so just confirm FinabApp
    # is NOT instantiated. Patch it to raise on construct.
    import finab.tui.app as tui_app_mod
    class ExplodingApp:
        def __init__(self):
            raise AssertionError("FinabApp should not be constructed when FINAB_TUI is unset")
    monkeypatch.setattr(tui_app_mod, "FinabApp", ExplodingApp)

    # The existing main() does real network init — patch the YNAB client construction
    # to short-circuit before it can fail on missing credentials.
    import finab.main as main_mod
    class FakeYnabClient:
        def __init__(self): raise RuntimeError("stop here — flag was unset, CLI path taken")
    monkeypatch.setattr(main_mod, "YNABClient", FakeYnabClient)

    from finab.main import main
    # The CLI path will hit our FakeYnabClient and abort — that's fine.
    # The important thing is that ExplodingApp is NEVER constructed.
    main()  # prints "Failed to initialize YNAB Client: stop here..." and returns
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_app.py::test_main_launches_tui_when_flag_set -v`
Expected: FAIL — `main` doesn't currently check the env var.

- [ ] **Step 3: Modify main()**

Edit `src/finab/main.py`. Find the `def main():` function (around line 627 in the post-Plan-1 file). At the very top of the function, before any other code, add:

```python
def main():
    import os
    if os.environ.get("FINAB_TUI"):
        from finab.tui.app import FinabApp
        FinabApp().run()
        return

    # --- existing CLI flow below ---
    load_dotenv()
    ...
```

(Keep everything else in `main()` unchanged — just prepend the env check.)

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: all tests pass — both the new entrypoint tests + the existing 5.

- [ ] **Step 5: Manual sanity check (no commit needed)**

Run: `FINAB_TUI=1 uv run finab` for ~3 seconds, then press `q`.

Expected: the sidebar with five screens appears, you can press down/up to navigate between them, `q` quits cleanly. If the app crashes or hangs, investigate before proceeding.

If you don't have a real terminal (e.g., subagent context), skip this step and note it in the report.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: 181 passing.

- [ ] **Step 7: Commit**

```bash
git add src/finab/main.py tests/tui/test_app.py
git commit -m "feat(tui): FINAB_TUI env flag launches the TUI"
```

---

## Task 5: Async data loader

The Sync screen needs FW transactions + YNAB transactions + YNAB categories before it can build a `SyncEngine`. Load these in parallel via Textual workers. The TUI shows a "Loading..." state until the data arrives.

**Files:**
- Create: `src/finab/tui/data_loader.py`
- Modify: `src/finab/tui/app.py`
- Create: `tests/tui/test_data_loader.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_data_loader.py`:

```python
"""Tests for the async data loader.

The real loader calls FinWiseClient and YNABClient over the network.
These tests inject fake clients and verify the loader collects the
right data and surfaces errors correctly.
"""
import pytest
from finab.tui.data_loader import LoadedData, load_all


class _FakeFwClient:
    def __init__(self, accounts=None, transactions=None, raise_on=None):
        self._accounts = accounts or []
        self._transactions = transactions or []
        self._raise_on = raise_on
    def get_accounts(self):
        if self._raise_on == "accounts":
            raise RuntimeError("fw accounts fetch failed")
        return self._accounts
    def get_transactions(self, **kwargs):
        if self._raise_on == "transactions":
            raise RuntimeError("fw transactions fetch failed")
        return self._transactions


class _FakeYnabClient:
    def __init__(self, accounts=None, transactions=None, categories=None, category_groups=None, payees=None, raise_on=None):
        self._accounts = accounts or []
        self._transactions = transactions or []
        self._categories = categories or []
        self._category_groups = category_groups or []
        self._payees = payees or []
        self._raise_on = raise_on
    def get_accounts(self, budget_id):
        if self._raise_on == "ynab_accounts":
            raise RuntimeError("ynab accounts fetch failed")
        return self._accounts
    def get_transactions(self, budget_id):
        if self._raise_on == "ynab_transactions":
            raise RuntimeError("ynab transactions fetch failed")
        return self._transactions
    def get_categories(self, budget_id):
        return self._categories
    def get_category_groups_with_categories(self, budget_id):
        return self._category_groups
    def get_payees(self, budget_id):
        return self._payees


async def test_load_all_returns_loaded_data():
    fw = _FakeFwClient(accounts=["fw-acc-1"], transactions=["fw-txn-1"])
    ynab = _FakeYnabClient(
        accounts=["yn-acc-1"],
        transactions=["yn-txn-1"],
        categories=["cat-1"],
        category_groups=["cg-1"],
        payees=["payee-1"],
    )
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert isinstance(data, LoadedData)
    assert data.fw_accounts == ["fw-acc-1"]
    assert data.fw_transactions == ["fw-txn-1"]
    assert data.ynab_accounts == ["yn-acc-1"]
    assert data.ynab_transactions == ["yn-txn-1"]
    assert data.ynab_categories == ["cat-1"]
    assert data.ynab_category_groups == ["cg-1"]
    assert data.ynab_payees == ["payee-1"]
    assert data.error is None


async def test_load_all_captures_exception():
    fw = _FakeFwClient(raise_on="transactions")
    ynab = _FakeYnabClient()
    data = await load_all(fw_client=fw, ynab_client=ynab, budget_id="bid")
    assert data.error is not None
    assert "fw transactions fetch failed" in str(data.error)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_data_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'finab.tui.data_loader'`.

- [ ] **Step 3: Implement the loader**

Write `src/finab/tui/data_loader.py`:

```python
"""Async data loader for the TUI.

`load_all` calls the seven data-fetch methods sequentially inside an
async function. Sequential, not parallel — the SDK clients are sync
(httpx-backed under the hood), and parallelizing inside one Textual
worker would require wrapping each call in run_in_executor, which is
more ceremony than it's worth for this volume of work. If load time
becomes a concern, parallelize with asyncio.to_thread per call.

All exceptions are caught and surfaced via LoadedData.error so the
TUI can show a banner instead of crashing.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LoadedData:
    """Bundled result of all data fetches needed by the TUI on boot."""
    fw_accounts: list = field(default_factory=list)
    fw_transactions: list = field(default_factory=list)
    ynab_accounts: list = field(default_factory=list)
    ynab_transactions: list = field(default_factory=list)
    ynab_categories: list = field(default_factory=list)
    ynab_category_groups: list = field(default_factory=list)
    ynab_payees: list = field(default_factory=list)
    error: Optional[Exception] = None


async def load_all(*, fw_client, ynab_client, budget_id: str) -> LoadedData:
    """Fetch everything the TUI needs on boot. Returns LoadedData.

    On any exception, returns LoadedData with `error` populated and
    partial data — callers can still render an error banner over
    whichever screens did get data.
    """
    data = LoadedData()
    try:
        data.fw_accounts = fw_client.get_accounts()
        data.fw_transactions = fw_client.get_transactions()
        data.ynab_accounts = ynab_client.get_accounts(budget_id)
        data.ynab_transactions = ynab_client.get_transactions(budget_id)
        data.ynab_categories = ynab_client.get_categories(budget_id)
        data.ynab_category_groups = ynab_client.get_category_groups_with_categories(budget_id)
        data.ynab_payees = ynab_client.get_payees(budget_id)
    except Exception as e:
        data.error = e
    return data
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_data_loader.py -v`
Expected: PASS — both tests.

- [ ] **Step 5: Wire the loader into FinabApp**

Edit `src/finab/tui/app.py`. Add imports and a worker method to FinabApp. The full file should look like this after the edit:

```python
"""FinabApp — root Textual application.

Layout: sidebar (left) + content switcher (right). On mount, kicks off
a background worker that fetches FW + YNAB data. The Sync screen waits
on that data; placeholder screens don't care.
"""
import os

from textual import work
from textual.app import App, ComposeResult
from textual.containers import ContentSwitcher, Horizontal
from textual.widgets import Label, ListItem, ListView

from finab.tui.data_loader import LoadedData, load_all
from finab.tui.screens.placeholder import PlaceholderScreen


SCREEN_IDS = [
    ("Sync", "screen-sync"),
    ("Accounts", "screen-accounts"),
    ("Merchants", "screen-merchants"),
    ("Memory", "screen-memory"),
    ("Settings", "screen-settings"),
]


class FinabApp(App):
    """Root app: sidebar nav + content switcher."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(self, *, fw_client=None, ynab_client=None, budget_id: str = None):
        """Construct. Clients and budget_id are injectable for tests; in
        production they default to real values built from .env."""
        super().__init__()
        self._fw_client = fw_client
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self.loaded: LoadedData | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(
                *[ListItem(Label(name), id=f"item-{sid}") for name, sid in SCREEN_IDS],
                id="sidebar",
            )
            with ContentSwitcher(initial="screen-sync", id="content-switcher"):
                for name, sid in SCREEN_IDS:
                    yield PlaceholderScreen(name, id=sid)

    def on_mount(self) -> None:
        """After the layout is mounted, kick off the data fetch — but
        only if clients were provided. Tests that don't provide clients
        get a TUI shell with no data, which is fine."""
        if self._fw_client and self._ynab_client and self._budget_id:
            self._kickoff_load()

    @work(exclusive=True)
    async def _kickoff_load(self) -> None:
        self.loaded = await load_all(
            fw_client=self._fw_client,
            ynab_client=self._ynab_client,
            budget_id=self._budget_id,
        )
        # Sync screen will be wired up to react to self.loaded in Task 6.

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        item_id = event.item.id
        if item_id and item_id.startswith("item-"):
            screen_id = item_id.removeprefix("item-")
            switcher = self.query_one("#content-switcher", ContentSwitcher)
            switcher.current = screen_id
```

Then update the entrypoint in `src/finab/main.py` to pass clients to `FinabApp`. Find the `if os.environ.get("FINAB_TUI"):` block from Task 4 and expand it:

```python
def main():
    import os
    if os.environ.get("FINAB_TUI"):
        from dotenv import load_dotenv
        load_dotenv()
        from finab.client import FinWiseClient
        from finab.ynab_client import YNABClient
        from finab.config import load_budget_id
        from finab.tui.app import FinabApp
        FinabApp(
            fw_client=FinWiseClient(),
            ynab_client=YNABClient(),
            budget_id=load_budget_id(),
        ).run()
        return

    # --- existing CLI flow below ---
    load_dotenv()
    ...
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/tui/ -v`
Expected: all tests pass. The two `FinabApp` boot tests (from Task 2/3) still work because `FinabApp()` with no client args is valid — the `on_mount` skips the load.

- [ ] **Step 7: Run the full suite**

Run: `uv run pytest`
Expected: 183 passing.

- [ ] **Step 8: Commit**

```bash
git add src/finab/tui/data_loader.py src/finab/tui/app.py src/finab/main.py tests/tui/test_data_loader.py
git commit -m "feat(tui): async data loader with FinabApp on_mount worker"
```

---

## Task 6: SyncScreen layout (master/detail panes, no interactivity yet)

Replace the `PlaceholderScreen` instance for Sync with a real `SyncScreen` container that has two panes: pending list (left, ~30 chars wide) and transaction card (right, takes the rest). For now, both render placeholder text — Task 7 + 8 add real widgets.

**Files:**
- Create: `src/finab/tui/screens/sync.py`
- Modify: `src/finab/tui/app.py` (swap PlaceholderScreen for SyncScreen on the Sync slot)
- Modify: `src/finab/tui/styles.tcss` (add SyncScreen styles)
- Create: `tests/tui/test_sync_screen.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_sync_screen.py`:

```python
"""Pilot-driven tests for the Sync screen."""
import pytest


@pytest.mark.asyncio
async def test_sync_screen_has_two_panes():
    """SyncScreen has a pending-list pane (left) and a detail pane (right)."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The Sync screen is the default; query inside it.
        pending_pane = app.query_one("#sync-pending")
        detail_pane = app.query_one("#sync-detail")
        assert pending_pane is not None
        assert detail_pane is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/tui/test_sync_screen.py -v`
Expected: FAIL — neither `#sync-pending` nor `#sync-detail` exists.

- [ ] **Step 3: Create SyncScreen container**

Write `src/finab/tui/screens/sync.py`:

```python
"""Sync screen — phase 3 transaction processing.

Layout: master/detail.
  Left pane (#sync-pending): scrollable list of candidates with status glyphs.
  Right pane (#sync-detail): the currently-selected candidate's details.

Plan 2 Task 6 lays down the layout with placeholder content. Later tasks
add the PendingList widget, TransactionCard widget, modals, and engine
wiring.
"""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static


class SyncScreen(Container):
    """The Sync screen container — embedded in FinabApp's ContentSwitcher."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Static("(no candidates yet)", id="sync-pending")
            yield Static("(select a transaction)", id="sync-detail")
```

- [ ] **Step 4: Mount SyncScreen in FinabApp**

Edit `src/finab/tui/app.py`. Import SyncScreen:

```python
from finab.tui.screens.sync import SyncScreen
```

Then change the `compose` method's content switcher loop. Currently:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    for name, sid in SCREEN_IDS:
        yield PlaceholderScreen(name, id=sid)
```

Replace with:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    for name, sid in SCREEN_IDS[1:]:  # skip Sync
        yield PlaceholderScreen(name, id=sid)
```

- [ ] **Step 5: Add styles for the SyncScreen panes**

Append to `src/finab/tui/styles.tcss`:

```tcss
SyncScreen {
    layout: horizontal;
    width: 1fr;
    height: 1fr;
}

SyncScreen Horizontal {
    width: 1fr;
    height: 1fr;
}

#sync-pending {
    width: 32;
    border-right: solid $primary;
    padding: 0 1;
    overflow-y: auto;
}

#sync-detail {
    width: 1fr;
    padding: 1 2;
}
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/tui/ -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/screens/sync.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_sync_screen.py
git commit -m "feat(tui): SyncScreen with master/detail layout placeholders"
```

---

## Task 7: PendingList widget

Replace the left pane's placeholder Static with a real `PendingList` widget that displays one row per `Candidate`. Each row shows: status glyph + merchant alias (or "(no merchant)") + amount. The widget exposes a `cursor_index` and an `on_selected` callback.

**Files:**
- Create: `src/finab/tui/widgets/pending_list.py`
- Modify: `src/finab/tui/screens/sync.py` (swap the Static for PendingList)
- Create: `tests/tui/test_pending_list.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_pending_list.py`:

```python
"""Unit-ish tests for PendingList. Constructs candidates synthetically
to avoid running merge_and_filter_transactions."""
import pytest


def _make_candidate(*, alias, amount, status="pending", auto_reason=None):
    """Build a Candidate stub for rendering — only the fields PendingList
    reads from txn are populated."""
    from finab.engine.sync import Candidate

    class FakeTxn:
        pass
    txn = FakeTxn()
    txn.amount = amount
    txn._alias = alias  # synthetic — PendingList reads via callable
    return Candidate(id=f"cid-{alias}-{amount}", txn=txn, status=status, auto_reason=auto_reason)


@pytest.mark.asyncio
async def test_pending_list_renders_candidates():
    """A PendingList given 3 candidates renders 3 rows with the expected glyphs."""
    from textual.app import App
    from finab.tui.widgets.pending_list import PendingList

    candidates = [
        _make_candidate(alias="Amazon", amount=-2399, status="decided"),
        _make_candidate(alias="Costco", amount=-8421, status="pending"),
        _make_candidate(alias="Salary", amount=150000, status="auto", auto_reason="inflow"),
    ]

    def alias_of(c):
        return c.txn._alias

    class _Host(App):
        def compose(self):
            yield PendingList(candidates=candidates, alias_of=alias_of, id="pl")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        pl = app.query_one("#pl", PendingList)
        rows = pl.row_glyphs_and_text()  # helper exposed by PendingList for testing
        assert len(rows) == 3
        # Decided
        assert rows[0][0] == "✓"
        assert "Amazon" in rows[0][1]
        assert "-23.99" in rows[0][1]
        # Pending
        assert rows[1][0] == "○"
        assert "Costco" in rows[1][1]
        # Auto inflow
        assert rows[2][0] == "+"
        assert "Salary" in rows[2][1]
        assert "1500.00" in rows[2][1]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/tui/test_pending_list.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement PendingList**

Write `src/finab/tui/widgets/pending_list.py`:

```python
"""PendingList — scrollable list of candidates with status glyphs.

This is a thin wrapper over Textual's ListView. Each ListItem renders
"GLYPH  ALIAS  AMOUNT" with glyph chosen by the candidate's status and
auto_reason. The cursor (which row is highlighted) is owned by ListView.
"""
from typing import Callable, Iterable, Optional

from textual.app import ComposeResult
from textual.widgets import Label, ListItem, ListView

from finab.engine.sync import Candidate


# Status glyph mapping. Matches the spec.
_GLYPHS = {
    ("pending", None): "○",
    ("decided", None): "✓",
    ("auto", "inflow"): "+",
    ("auto", "transfer"): "→",
    ("auto", "no-merchant"): "✗",
    ("auto", "pre-month"): "↷",
    ("flushed", None): "⇡",
}


def _glyph_for(candidate: Candidate) -> str:
    """Pick the row glyph from candidate.status + candidate.auto_reason."""
    # status=flushed and decided ignore auto_reason; auto requires it.
    key_specific = (candidate.status, candidate.auto_reason)
    if key_specific in _GLYPHS:
        return _GLYPHS[key_specific]
    return _GLYPHS.get((candidate.status, None), "?")


def _amount_str(amount_milliunits: int) -> str:
    """Render milliunits as a signed currency string with 2 decimals."""
    return f"{amount_milliunits / 1000:.2f}"


class PendingList(ListView):
    """ListView showing one ListItem per candidate. Candidates are passed
    in via constructor; the widget never re-fetches.

    alias_of(candidate) -> str: how to display the candidate's merchant
    alias. The TUI passes a closure that looks up the alias from the
    ConfigStore; tests pass a function that reads from a synthetic field.
    """

    def __init__(
        self,
        *,
        candidates: Iterable[Candidate],
        alias_of: Callable[[Candidate], str],
        id: Optional[str] = None,
    ):
        self._candidates = list(candidates)
        self._alias_of = alias_of
        items = [self._row(c) for c in self._candidates]
        super().__init__(*items, id=id)

    def _row(self, candidate: Candidate) -> ListItem:
        glyph = _glyph_for(candidate)
        alias = self._alias_of(candidate) or "(no merchant)"
        amount = _amount_str(candidate.txn.amount)
        text = f"{glyph}  {alias:<18.18}  {amount:>10}"
        return ListItem(Label(text), id=f"row-{candidate.id}")

    @property
    def candidates(self) -> list[Candidate]:
        return list(self._candidates)

    def current_candidate(self) -> Optional[Candidate]:
        """The candidate the cursor is on, or None if the list is empty."""
        idx = self.index
        if idx is None or idx < 0 or idx >= len(self._candidates):
            return None
        return self._candidates[idx]

    def refresh_row(self, candidate_id: str) -> None:
        """Rebuild the row for a candidate whose state changed (e.g.,
        after engine.apply_category). Looks up the candidate by id."""
        for i, c in enumerate(self._candidates):
            if c.id == candidate_id:
                # Remove old, insert new at same index. ListView doesn't
                # have a single 'replace at index' API, so we delete and
                # mount at position.
                self.remove_items([i])
                new_item = self._row(c)
                self.insert_items(i, [new_item])
                return

    # ---- test helpers ----
    def row_glyphs_and_text(self) -> list[tuple[str, str]]:
        """Return [(glyph, full_text), ...] for testing.

        Reads from the ListItem label renderables, NOT from the source
        candidate state — so tests verify what the user actually sees."""
        result = []
        for item in self.children:
            label = item.query_one(Label)
            text = str(label.renderable)
            glyph = text.split()[0] if text else ""
            result.append((glyph, text))
        return result
```

Important: Textual's `ListView` exposes `remove_items` and `insert_items` methods to mutate the list — verify against the installed version. If those don't exist, fall back to `await self.remove_children(...)` + `await self.mount(...)` (these are async). Implementer: check `uv run python -c "from textual.widgets import ListView; print([m for m in dir(ListView) if not m.startswith('_')])"` and use whatever methods are available; the goal is just "rebuild one row". If both APIs are unavailable, the fallback is to recompose the entire ListView — slower but correct. Document whichever path you use with a comment.

- [ ] **Step 4: Wire PendingList into SyncScreen**

Edit `src/finab/tui/screens/sync.py`:

```python
"""Sync screen — phase 3 transaction processing.

Layout: master/detail.
  Left pane (#sync-pending): PendingList — candidates with status glyphs.
  Right pane (#sync-detail): TransactionCard — selected candidate details.

Plan 2 Task 7: PendingList is wired up but receives an empty candidate
list (engine wiring comes in Task 9). Visual smoke test only at this
stage.
"""
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static

from finab.tui.widgets.pending_list import PendingList


def _placeholder_alias_of(candidate) -> str:
    """Used when no engine is bound yet — returns a stub alias."""
    return getattr(candidate.txn, "_alias", "?")


class SyncScreen(Container):
    """The Sync screen container — embedded in FinabApp's ContentSwitcher."""

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PendingList(
                candidates=[],
                alias_of=_placeholder_alias_of,
                id="sync-pending",
            )
            yield Static("(select a transaction)", id="sync-detail")
```

- [ ] **Step 5: Update Task 6's test (the IDs are still right)**

Re-run `tests/tui/test_sync_screen.py`. `#sync-pending` now resolves to a `PendingList` instead of a `Static` — but the test only checks `is not None`, so it still passes.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/tui/ -v`
Expected: all pass — the new `test_pending_list.py` tests are green, the existing ones still work.

- [ ] **Step 7: Run full suite**

Run: `uv run pytest`
Expected: 184 passing.

- [ ] **Step 8: Commit**

```bash
git add src/finab/tui/widgets/pending_list.py src/finab/tui/screens/sync.py tests/tui/test_pending_list.py
git commit -m "feat(tui): PendingList widget with status glyphs"
```

---

## Task 8: TransactionCard widget

The right pane shows the currently-selected candidate's details: merchant alias, date, amount, memo, status. When no candidate is selected (or list is empty), shows "(select a transaction)". The card subscribes to the parent screen's `current_candidate` changes.

**Files:**
- Create: `src/finab/tui/widgets/transaction_card.py`
- Modify: `src/finab/tui/screens/sync.py`
- Modify: `tests/tui/test_sync_screen.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_sync_screen.py`:

```python
@pytest.mark.asyncio
async def test_transaction_card_shows_empty_state():
    """No candidates → card shows empty-state text."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        card = app.query_one("#sync-detail")
        # When no candidate is selected, card content is empty-state.
        assert "select a transaction" in str(card.renderable).lower() or \
               "no transaction" in str(card.renderable).lower()


@pytest.mark.asyncio
async def test_transaction_card_renders_candidate():
    """Card renders the candidate's fields after one is selected."""
    from datetime import date
    from finab.engine.sync import Candidate
    from finab.tui.app import FinabApp

    class FakeTxn:
        amount = -8421
        memo = "COSTCO WHSE #1234"
        date = date(2026, 5, 22)
        category_id = None
        subtransactions = []

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Inject a candidate via the screen's API (we'll add it next).
        sync_screen = app.query_one("#screen-sync")
        sync_screen.set_candidates(
            [Candidate(id="abc", txn=FakeTxn(), status="pending")],
            alias_of=lambda c: "Costco",
        )
        await pilot.pause()
        card_text = str(app.query_one("#sync-detail").renderable)
        assert "Costco" in card_text
        assert "-84.21" in card_text
        assert "2026-05-22" in card_text
        assert "COSTCO WHSE" in card_text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_sync_screen.py -v`
Expected: the new tests fail — TransactionCard doesn't exist yet and `set_candidates` isn't on SyncScreen.

- [ ] **Step 3: Implement TransactionCard**

Write `src/finab/tui/widgets/transaction_card.py`:

```python
"""TransactionCard — detail view of a single Candidate.

Updates via `set_candidate(c, alias_of)` from the parent screen. When
called with None, shows the empty state.
"""
from typing import Callable, Optional

from textual.widgets import Static

from finab.engine.sync import Candidate


_STATUS_LABELS = {
    "pending": "pending — needs decision",
    "decided": "decided",
    "auto": "auto-resolved",
    "flushed": "pushed to YNAB",
}


def _amount_str(amount_milliunits: int) -> str:
    return f"{amount_milliunits / 1000:.2f}"


class TransactionCard(Static):
    """A read-only render of a Candidate's details."""

    def set_candidate(
        self,
        candidate: Optional[Candidate],
        *,
        alias_of: Callable[[Candidate], str] = None,
    ) -> None:
        """Re-render to show this candidate. None clears to empty state."""
        if candidate is None:
            self.update("(select a transaction)")
            return
        txn = candidate.txn
        alias = (alias_of(candidate) if alias_of else None) or "(no merchant)"
        amount = _amount_str(getattr(txn, "amount", 0))
        date = getattr(txn, "date", "?")
        memo = getattr(txn, "memo", "") or "(no memo)"
        status_extra = ""
        if candidate.auto_reason:
            status_extra = f" ({candidate.auto_reason})"
        status_label = _STATUS_LABELS.get(candidate.status, candidate.status) + status_extra
        lines = [
            f"Merchant:  {alias}",
            f"Date:      {date}",
            f"Amount:    {amount}",
            f"Memo:      {memo}",
            f"Status:    {status_label}",
        ]
        self.update("\n".join(lines))
```

- [ ] **Step 4: Wire TransactionCard into SyncScreen + add set_candidates API**

Overwrite `src/finab/tui/screens/sync.py`:

```python
"""Sync screen — phase 3 transaction processing.

Layout: master/detail. The screen owns the candidates list and the
alias-lookup callable; widgets are dumb views of that state.
"""
from typing import Callable, Iterable, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import ListView

from finab.engine.sync import Candidate
from finab.tui.widgets.pending_list import PendingList
from finab.tui.widgets.transaction_card import TransactionCard


def _placeholder_alias_of(candidate) -> str:
    return getattr(candidate.txn, "_alias", "?")


class SyncScreen(Container):
    """The Sync screen container — embedded in FinabApp's ContentSwitcher."""

    def __init__(self, *, id: str = None):
        super().__init__(id=id)
        self._candidates: list[Candidate] = []
        self._alias_of: Callable[[Candidate], str] = _placeholder_alias_of

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PendingList(
                candidates=[],
                alias_of=_placeholder_alias_of,
                id="sync-pending",
            )
            yield TransactionCard("(select a transaction)", id="sync-detail")

    def set_candidates(
        self,
        candidates: Iterable[Candidate],
        *,
        alias_of: Callable[[Candidate], str],
    ) -> None:
        """Replace the screen's candidate list. Rebuilds PendingList
        and refreshes the card.

        Used by FinabApp after data loading completes (Task 9) and by
        tests to inject synthetic candidates."""
        self._candidates = list(candidates)
        self._alias_of = alias_of

        # Rebuild PendingList by remounting (simplest approach — N is
        # small in practice).
        old = self.query_one("#sync-pending", PendingList)
        new = PendingList(
            candidates=self._candidates,
            alias_of=alias_of,
            id="sync-pending",
        )
        old.remove()
        # Insert at the same position. Horizontal containers don't have
        # explicit position APIs; mount() appends, then we move via
        # CSS order if needed. For this layout the order is left-then-right
        # and the card was second, so we mount before the card.
        horiz = self.query_one(Horizontal)
        horiz.mount(new, before=self.query_one("#sync-detail"))

        # Refresh detail with the first candidate (if any).
        card = self.query_one("#sync-detail", TransactionCard)
        if self._candidates:
            card.set_candidate(self._candidates[0], alias_of=alias_of)
        else:
            card.set_candidate(None)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """When the cursor in PendingList moves, refresh the detail card."""
        pl = self.query_one("#sync-pending", PendingList)
        if event.list_view is not pl:
            return  # not our list
        current = pl.current_candidate()
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(current, alias_of=self._alias_of)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/tui/test_sync_screen.py -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: 186 passing.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/widgets/transaction_card.py src/finab/tui/screens/sync.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): TransactionCard widget + SyncScreen.set_candidates"
```

---

## Task 9: Engine integration — construct SyncEngine on data load

When `FinabApp._kickoff_load` completes successfully, instantiate a `SyncEngine` from the loaded data and call `SyncScreen.set_candidates(engine.candidates, alias_of=...)`. Also stash the engine on the screen so subsequent task can call `engine.apply_*` / `flush` etc.

**Files:**
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/screens/sync.py`
- Modify: `tests/tui/test_sync_screen.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/tui/test_sync_screen.py`:

```python
@pytest.mark.asyncio
async def test_sync_screen_builds_engine_from_loaded_data(tmp_path, monkeypatch):
    """When FinabApp finishes loading data, SyncScreen has a SyncEngine
    and renders the engine's candidates."""
    from datetime import date as date_cls
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.models import Transaction
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData

    # Seed a ConfigStore so the engine finds a mapped account.
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    txn = Transaction(
        import_id="fw-1",
        amount=-8421,
        date=date_cls.today(),
        memo="COSTCO",
        merchant_id=None,
        account_id="fw-acc-1",
    )
    pre_loaded = LoadedData(
        fw_accounts=[],
        fw_transactions=[txn],
        ynab_accounts=[],
        ynab_transactions=[],
        ynab_categories=[],
        ynab_category_groups=[],
        ynab_payees=[],
    )

    app = FinabApp()
    # Inject the store + tx_store + pre-loaded data manually (skip the worker).
    app._store = store
    app._tx_store = tx_store

    async with app.run_test() as pilot:
        await pilot.pause()
        # Manually feed the data into the screen (simulates worker completion).
        from finab.tui.screens.sync import SyncScreen
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=pre_loaded, store=store, tx_store=tx_store)
        await pilot.pause()
        # One candidate should be present (the unmatched-merchant txn auto-resolves to no-merchant).
        from finab.tui.widgets.pending_list import PendingList
        pl = app.query_one("#sync-pending", PendingList)
        assert len(pl.candidates) == 1
        assert pl.candidates[0].status == "auto"
        assert pl.candidates[0].auto_reason == "no-merchant"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/tui/test_sync_screen.py::test_sync_screen_builds_engine_from_loaded_data -v`
Expected: FAIL — `bind_data` doesn't exist on SyncScreen.

- [ ] **Step 3: Implement bind_data on SyncScreen**

Edit `src/finab/tui/screens/sync.py`. Add at the top:

```python
from finab.engine.sync import SyncEngine
```

Add the method to the class:

```python
    def bind_data(self, *, loaded, store, tx_store) -> None:
        """Build a SyncEngine from loaded data and push its candidates
        into the view. The screen retains references to the engine and
        store so subsequent actions (apply, undo, flush) can dispatch."""
        self._store = store
        self._tx_store = tx_store
        self._engine = SyncEngine(
            fw_transactions=loaded.fw_transactions,
            ynab_transactions=loaded.ynab_transactions,
            ynab_categories=loaded.ynab_categories,
            store=store,
            tx_store=tx_store,
        )

        def alias_of(candidate):
            merchant_id = getattr(candidate.txn, "merchant_id", None)
            if not merchant_id:
                return None
            merchant = store.merchant_by_finwise_id(merchant_id)
            return merchant.get("alias") if merchant else None

        self.set_candidates(self._engine.candidates, alias_of=alias_of)
```

Also initialize the attributes in `__init__`:

```python
    def __init__(self, *, id: str = None):
        super().__init__(id=id)
        self._candidates: list[Candidate] = []
        self._alias_of: Callable[[Candidate], str] = _placeholder_alias_of
        self._engine = None
        self._store = None
        self._tx_store = None
```

- [ ] **Step 4: Wire FinabApp to call bind_data after loading**

Edit `src/finab/tui/app.py`. In `__init__`, accept `store` and `tx_store` (default None):

```python
    def __init__(self, *, fw_client=None, ynab_client=None, budget_id: str = None, store=None, tx_store=None):
        super().__init__()
        self._fw_client = fw_client
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self._store = store
        self._tx_store = tx_store
        self.loaded: LoadedData | None = None
```

Update `_kickoff_load` to call `bind_data` after the load finishes:

```python
    @work(exclusive=True)
    async def _kickoff_load(self) -> None:
        self.loaded = await load_all(
            fw_client=self._fw_client,
            ynab_client=self._ynab_client,
            budget_id=self._budget_id,
        )
        if self.loaded.error is None and self._store and self._tx_store:
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.bind_data(
                loaded=self.loaded,
                store=self._store,
                tx_store=self._tx_store,
            )
```

Update `main.py` entrypoint to also pass `store` and `tx_store`:

```python
def main():
    import os
    if os.environ.get("FINAB_TUI"):
        from dotenv import load_dotenv
        load_dotenv()
        from finab.client import FinWiseClient
        from finab.ynab_client import YNABClient
        from finab.config import load_budget_id
        from finab.store import ConfigStore
        from finab.transactions import TransactionsStore
        from finab.tui.app import FinabApp
        FinabApp(
            fw_client=FinWiseClient(),
            ynab_client=YNABClient(),
            budget_id=load_budget_id(),
            store=ConfigStore(),
            tx_store=TransactionsStore(),
        ).run()
        return

    # --- existing CLI flow below ---
    ...
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/tui/ -v`
Expected: all pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: 187 passing.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/screens/sync.py src/finab/tui/app.py src/finab/main.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): wire SyncEngine to SyncScreen after data load"
```

---

## Task 10: Category picker modal

Add `CategoryPickerModal` — a `ModalScreen` with an `Input` filter at top and an `OptionList` of categories below, ranked: merchant's used-categories first (with `(Nx)` frequency), then everything else. Selection dismisses the modal with the chosen category_id. Cancellation dismisses with `None`.

**Files:**
- Create: `src/finab/tui/widgets/category_picker.py`
- Create: `tests/tui/test_category_picker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_category_picker.py`:

```python
"""Tests for the CategoryPickerModal.

Each test runs the modal inside a host App to test dismiss-with-result."""
import pytest


class _FakeCategory:
    def __init__(self, id, name, *, hidden=False, deleted=False):
        self.id = id
        self.name = name
        self.hidden = hidden
        self.deleted = deleted


@pytest.mark.asyncio
async def test_category_picker_dismisses_with_selected_id():
    """Picking the first row dismisses with that category's id."""
    from textual.app import App
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [
        _FakeCategory("cat-groc", "Groceries"),
        _FakeCategory("cat-house", "Household"),
    ]
    used = {"cat-groc": 18}

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = CategoryPickerModal(
                categories=categories,
                used_categories=used,
                merchant_alias="Costco",
            )
            self.push_screen(modal, callback=self._on_dismiss)

        def _on_dismiss(self, result):
            result_holder["value"] = result

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The first option is the most-used (Groceries). Press Enter.
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder["value"] == "cat-groc"


@pytest.mark.asyncio
async def test_category_picker_dismisses_with_none_on_escape():
    from textual.app import App
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [_FakeCategory("cat-groc", "Groceries")]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                CategoryPickerModal(categories=categories, used_categories={}, merchant_alias="?"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None


@pytest.mark.asyncio
async def test_category_picker_filters_on_input():
    """Typing in the input narrows the OptionList to matching categories."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [
        _FakeCategory("cat-groc", "Groceries"),
        _FakeCategory("cat-house", "Household"),
        _FakeCategory("cat-gas", "Gas"),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                CategoryPickerModal(categories=categories, used_categories={}, merchant_alias="?"),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Type into the input.
        await pilot.press("g", "r", "o")
        await pilot.pause()
        # OptionList should now show only Groceries.
        ol = app.screen.query_one(OptionList)
        # Count visible options. OptionList.option_count is the loaded total.
        # We assert by reading visible option text.
        opts = list(ol._options)  # internal but stable
        visible = [o.prompt for o in opts]
        # All visible should be Groceries (single match) — at least, none should be Gas/Household.
        assert all("groc" in str(p).lower() for p in visible), f"got: {visible}"
```

The internal `_options` access in the third test is fragile; if it breaks against the installed textual version, fall back to asserting on what's selectable via `ol.action_first()` + reading the highlighted item. The first two tests are the load-bearing ones.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_category_picker.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement CategoryPickerModal**

Write `src/finab/tui/widgets/category_picker.py`:

```python
"""CategoryPickerModal — fuzzy-search category picker.

Dismisses with the selected category_id (str), or None if cancelled.

Ranking:
  1. Merchant's used categories (sorted by frequency descending).
  2. Visual separator.
  3. All other non-hidden, non-deleted categories (alphabetical).

Filtering: substring match (case-insensitive) on category name.
"""
from typing import Mapping

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class CategoryPickerModal(ModalScreen[str | None]):
    """Modal that returns a category_id (str) or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        categories: list,
        used_categories: Mapping[str, int],
        merchant_alias: str,
    ):
        super().__init__()
        # Filter out hidden/deleted up front; we never want to show them.
        self._all = [
            c for c in categories
            if not getattr(c, "hidden", False) and not getattr(c, "deleted", False)
        ]
        self._used = dict(used_categories)
        self._merchant_alias = merchant_alias
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Static(f"Pick category for '{self._merchant_alias}'", id="picker-title")
            yield Input(placeholder="filter…", id="picker-filter")
            yield OptionList(id="picker-options")

    def on_mount(self) -> None:
        self._refresh_options()
        # Focus the input so typing filters immediately.
        self.query_one("#picker-filter", Input).focus()

    def _ranked_options(self) -> list[tuple[str, str]]:
        """Return [(category_id, display_text), ...] in ranked order.
        Filter is applied before ranking — used categories still rank
        first among matches."""
        f = self._filter_text.lower()
        used_matches = []
        other_matches = []
        for c in self._all:
            name = str(getattr(c, "name", ""))
            if f and f not in name.lower():
                continue
            cid = str(c.id)
            if cid in self._used:
                label = f"{name}  ({self._used[cid]}x for {self._merchant_alias})"
                used_matches.append((self._used[cid], cid, label))
            else:
                other_matches.append((cid, name))
        used_matches.sort(key=lambda t: (-t[0], t[2].lower()))
        other_matches.sort(key=lambda t: t[1].lower())
        out = [(cid, label) for _, cid, label in used_matches]
        out.extend(other_matches)
        return out

    def _refresh_options(self) -> None:
        ol = self.query_one("#picker-options", OptionList)
        ol.clear_options()
        for cid, label in self._ranked_options():
            ol.add_option(Option(label, id=cid))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User pressed Enter on a row — dismiss with that category's id."""
        if event.option.id:
            self.dismiss(event.option.id)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Pressing Enter inside the input acts as 'pick the highlighted row'."""
        ol = self.query_one("#picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        if opt.id:
            self.dismiss(opt.id)
```

Also add modal styling. Append to `src/finab/tui/styles.tcss`:

```tcss
CategoryPickerModal {
    align: center middle;
}

#picker-dialog {
    width: 70%;
    height: 80%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#picker-title {
    text-style: bold;
    padding-bottom: 1;
}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_category_picker.py -v`
Expected: PASS — all three tests. If the filter test fails on the `_options` access, adjust the assertion to use a stable API (or just check that the OptionList has fewer options after filter). The first two are the priority.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: 190 passing.

- [ ] **Step 6: Commit**

```bash
git add src/finab/tui/widgets/category_picker.py src/finab/tui/styles.tcss tests/tui/test_category_picker.py
git commit -m "feat(tui): CategoryPickerModal with fuzzy search"
```

---

## Task 11: Split editor modal

`SplitEditorModal` — a `ModalScreen` showing a live-editable table of `(amount, category, memo)` rows. A "Remaining" label updates as the user types or picks. Confirm (`Ctrl+S`) is enabled only when remaining is zero. Dismisses with a `list[dict]` (the splits) or `None`.

The picker integration here is non-trivial: when the user picks the category for a row, we push the `CategoryPickerModal` and dismiss it back into this modal. Two-modal stacking.

**Files:**
- Create: `src/finab/tui/widgets/split_editor.py`
- Create: `tests/tui/test_split_editor.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/tui/test_split_editor.py`:

```python
"""Tests for SplitEditorModal — focus on the result it dismisses with."""
import pytest


class _FakeCategory:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.hidden = False
        self.deleted = False


@pytest.mark.asyncio
async def test_split_editor_initial_state():
    """A fresh modal shows one row holding the full transaction amount."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                SplitEditorModal(
                    txn_amount=-8421,
                    categories=[_FakeCategory("cat-x", "Generic")],
                    used_categories={},
                    merchant_alias="Costco",
                ),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        rows = modal.current_rows()  # helper exposed for testing
        assert len(rows) == 1
        assert rows[0]["amount"] == -8421
        assert modal.remaining_milliunits() == 0  # one row holds the full total


@pytest.mark.asyncio
async def test_split_editor_dismisses_with_splits_when_balanced(monkeypatch):
    """Programmatically add rows, then confirm — dismisses with a list of dicts."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = SplitEditorModal(
                txn_amount=-8000,
                categories=[_FakeCategory("cat-a", "A"), _FakeCategory("cat-b", "B")],
                used_categories={},
                merchant_alias="?",
            )
            # Manually populate rows for the test (bypassing the picker UI).
            modal.set_rows([
                {"amount": -5000, "category_id": "cat-a", "memo": "a"},
                {"amount": -3000, "category_id": "cat-b", "memo": "b"},
            ])
            self.push_screen(modal, callback=lambda r: result_holder.__setitem__("value", r))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Press the confirm action.
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert result_holder["value"] == [
        {"amount": -5000, "category_id": "cat-a", "memo": "a"},
        {"amount": -3000, "category_id": "cat-b", "memo": "b"},
    ]


@pytest.mark.asyncio
async def test_split_editor_refuses_confirm_when_unbalanced(monkeypatch):
    """Sum != total → ctrl+s is a no-op (remains open)."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = SplitEditorModal(
                txn_amount=-8000,
                categories=[_FakeCategory("cat-a", "A")],
                used_categories={},
                merchant_alias="?",
            )
            modal.set_rows([
                {"amount": -3000, "category_id": "cat-a", "memo": ""},
            ])
            self.push_screen(modal, callback=lambda r: result_holder.__setitem__("value", r))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Modal should still be open (not dismissed).
        assert result_holder["value"] == "not-set"
        # And the remaining value is non-zero.
        modal = app.screen
        assert modal.remaining_milliunits() != 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_split_editor.py -v`
Expected: FAIL — modal doesn't exist.

- [ ] **Step 3: Implement SplitEditorModal**

Write `src/finab/tui/widgets/split_editor.py`:

```python
"""SplitEditorModal — live-editable split table.

State model: a list of rows; each row is {amount: int milliunits,
category_id: str | None, memo: str}. Initial state: one row holding
the full transaction amount, no category.

For Plan 2, the table is rendered as a simple list of editable rows
each comprising a label-formatted line. Full inline editing (per-cell
input widgets in a DataTable) is overkill at this stage — we instead
use a Static showing the current state, and a single Input at the
bottom that accepts commands ('add row', 'edit N <amount> <cat> <memo>',
etc.). Bare-bones but functional; Plan 3 may upgrade to a DataTable.

External API for tests:
  - current_rows() -> list[dict]
  - set_rows(rows) — replace the whole row set (bypasses UI for tests)
  - remaining_milliunits() -> int (zero when balanced)
"""
from typing import Mapping, Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


def _fmt(milli: int) -> str:
    return f"{milli / 1000:.2f}"


class SplitEditorModal(ModalScreen[Optional[list]]):
    """Returns a list[dict] of splits on confirm, or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
        ("ctrl+s", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        *,
        txn_amount: int,
        categories: list,
        used_categories: Mapping[str, int],
        merchant_alias: str,
    ):
        super().__init__()
        self._txn_amount = txn_amount
        self._categories = categories
        self._used = dict(used_categories)
        self._merchant_alias = merchant_alias
        # Start with one row holding the full amount, no category.
        self._rows: list[dict] = [
            {"amount": txn_amount, "category_id": None, "memo": ""}
        ]

    def compose(self) -> ComposeResult:
        with Vertical(id="split-dialog"):
            yield Static(
                f"Split {self._merchant_alias} — total: {_fmt(self._txn_amount)}",
                id="split-title",
            )
            yield Static("", id="split-rows")
            yield Static("", id="split-remaining")
            yield Static(
                "  Commands: add <amount> <cat-id> [memo] | del N | edit N <amount> | cat N <cat-id> | Ctrl+S confirm | Esc cancel",
                id="split-help",
            )
            yield Input(placeholder="command…", id="split-input")

    def on_mount(self) -> None:
        self._render_state()
        self.query_one("#split-input", Input).focus()

    # ---- public API for tests ----

    def current_rows(self) -> list[dict]:
        return list(self._rows)

    def set_rows(self, rows: list[dict]) -> None:
        self._rows = [dict(r) for r in rows]
        if self.is_mounted:
            self._render_state()

    def remaining_milliunits(self) -> int:
        return self._txn_amount - sum(r["amount"] for r in self._rows)

    # ---- rendering ----

    def _render_state(self) -> None:
        lines = []
        for i, row in enumerate(self._rows, start=1):
            cat = row["category_id"] or "(no cat)"
            memo = row["memo"] or ""
            lines.append(f"  {i}. {_fmt(row['amount']):>10}   cat={cat}   memo={memo}")
        self.query_one("#split-rows", Static).update("\n".join(lines) or "  (empty)")

        rem = self.remaining_milliunits()
        rem_text = f"  Remaining: {_fmt(rem)}"
        if rem == 0:
            rem_text += "  ✓ ready to confirm (Ctrl+S)"
        self.query_one("#split-remaining", Static).update(rem_text)

    # ---- actions ----

    def action_confirm(self) -> None:
        if self.remaining_milliunits() != 0:
            self.app.bell()
            return
        # Reject rows without a category — we'd push splits without category to YNAB.
        if any(r["category_id"] is None for r in self._rows):
            self.app.bell()
            return
        self.dismiss(self._rows)

    # ---- input handling ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "split-input":
            return
        raw = (event.value or "").strip()
        event.input.value = ""
        if not raw:
            return
        try:
            self._dispatch(raw)
        except ValueError as e:
            # Show error in remaining bar briefly; for Plan 2 we just bell.
            self.app.bell()
        self._render_state()

    def _dispatch(self, raw: str) -> None:
        parts = raw.split(maxsplit=3)
        cmd = parts[0].lower()
        if cmd == "add" and len(parts) >= 3:
            amt = int(round(float(parts[1]) * 1000))
            cat = parts[2]
            memo = parts[3] if len(parts) > 3 else ""
            self._rows.append({"amount": amt, "category_id": cat, "memo": memo})
            return
        if cmd == "del" and len(parts) == 2:
            n = int(parts[1])
            if 1 <= n <= len(self._rows):
                self._rows.pop(n - 1)
            return
        if cmd == "edit" and len(parts) >= 3:
            n = int(parts[1])
            if not (1 <= n <= len(self._rows)):
                raise ValueError("bad index")
            self._rows[n - 1]["amount"] = int(round(float(parts[2]) * 1000))
            return
        if cmd == "cat" and len(parts) >= 3:
            n = int(parts[1])
            if not (1 <= n <= len(self._rows)):
                raise ValueError("bad index")
            self._rows[n - 1]["category_id"] = parts[2]
            return
        raise ValueError(f"unknown command: {cmd!r}")
```

Add modal styling. Append to `src/finab/tui/styles.tcss`:

```tcss
SplitEditorModal {
    align: center middle;
}

#split-dialog {
    width: 80%;
    height: 80%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#split-title {
    text-style: bold;
    padding-bottom: 1;
}

#split-help {
    color: $text-muted;
}
```

A note on UX: the command-line interface in the split editor is intentionally minimal for Plan 2. It's verbose but explicit, makes the modal easy to test (just feed strings), and avoids the trap of building a half-baked DataTable. Plan 3 (or a later polish task) can replace this with proper cell editing. The functional path — confirm-when-balanced, reject-when-not — works regardless of input shape.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_split_editor.py -v`
Expected: PASS — all three tests.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: 193 passing.

- [ ] **Step 6: Commit**

```bash
git add src/finab/tui/widgets/split_editor.py src/finab/tui/styles.tcss tests/tui/test_split_editor.py
git commit -m "feat(tui): SplitEditorModal with balance invariant"
```

---

## Task 12: Repeat-from-history modal

`HistoryPickerModal` — `OptionList` over a merchant's `processings` dict, "closest to current amount" entry pre-highlighted. Dismisses with the chosen entry (a dict with `parent_memo` and `splits`), or `None` on cancel.

**Files:**
- Create: `src/finab/tui/widgets/history_picker.py`
- Create: `tests/tui/test_history_picker.py`

- [ ] **Step 1: Write the failing test**

Create `tests/tui/test_history_picker.py`:

```python
"""Tests for HistoryPickerModal."""
import pytest


@pytest.mark.asyncio
async def test_history_picker_dismisses_with_chosen_entry():
    """Pressing Enter on the highlighted row returns its (amount_key, entry) tuple."""
    from textual.app import App
    from finab.tui.widgets.history_picker import HistoryPickerModal

    processings = {
        "-8421": {"parent_memo": "weekly", "splits": [
            {"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}
        ]},
        "-1250": {"parent_memo": "gas", "splits": [
            {"category_id": "cat-gas", "amount_milliunits": -1250, "memo": ""}
        ]},
    }
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                HistoryPickerModal(processings=processings, txn_amount=-8000),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    # closest to -8000 is -8421 (diff 421) vs -1250 (diff 6750) → -8421 wins.
    assert result_holder["value"] is not None
    amount_key, entry = result_holder["value"]
    assert amount_key == "-8421"
    assert entry["parent_memo"] == "weekly"


@pytest.mark.asyncio
async def test_history_picker_dismisses_with_none_on_escape():
    from textual.app import App
    from finab.tui.widgets.history_picker import HistoryPickerModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                HistoryPickerModal(processings={"-100": {"parent_memo": "", "splits": []}}, txn_amount=-100),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
```

- [ ] **Step 2: Run the tests**

Run: `uv run pytest tests/tui/test_history_picker.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Implement HistoryPickerModal**

Write `src/finab/tui/widgets/history_picker.py`:

```python
"""HistoryPickerModal — pick a prior processing for the current merchant.

Dismisses with (amount_key, entry) tuple, or None on cancel.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


def _fmt(milli: int) -> str:
    return f"{milli / 1000:.2f}"


def _closest_key(processings: dict, txn_amount: int) -> Optional[str]:
    """Return the processings key with amount closest to txn_amount, or
    None if processings is empty."""
    best = None
    best_diff = None
    for k in processings.keys():
        try:
            k_amt = int(k)
        except (TypeError, ValueError):
            continue
        diff = abs(k_amt - txn_amount)
        if best_diff is None or diff < best_diff:
            best = k
            best_diff = diff
    return best


class HistoryPickerModal(ModalScreen[Optional[tuple[str, dict]]]):
    """Returns (amount_key, entry_dict) tuple or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, processings: dict, txn_amount: int):
        super().__init__()
        self._processings = dict(processings)
        self._txn_amount = txn_amount

    def compose(self) -> ComposeResult:
        with Vertical(id="history-dialog"):
            yield Static("Prior categorizations", id="history-title")
            yield OptionList(id="history-options")

    def on_mount(self) -> None:
        ol = self.query_one("#history-options", OptionList)
        closest = _closest_key(self._processings, self._txn_amount)
        for k, entry in self._processings.items():
            try:
                amt = int(k) / 1000.0
                amt_str = f"{amt:>10.2f}"
            except (TypeError, ValueError):
                amt_str = f"{k:>10}"
            splits = entry.get("splits", []) or []
            if len(splits) == 1:
                label = f"{amt_str}   {splits[0].get('category_id', '?')}"
            else:
                label = f"{amt_str}   split ({len(splits)} categories)"
            if k == closest:
                label += "  (closest)"
            ol.add_option(Option(label, id=k))
        if closest is not None:
            # Move highlight to the closest row.
            keys = list(self._processings.keys())
            ol.highlighted = keys.index(closest)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        k = event.option.id
        if k is None or k not in self._processings:
            self.dismiss(None)
            return
        self.dismiss((k, self._processings[k]))
```

Add to `src/finab/tui/styles.tcss`:

```tcss
HistoryPickerModal {
    align: center middle;
}

#history-dialog {
    width: 70%;
    height: 60%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#history-title {
    text-style: bold;
    padding-bottom: 1;
}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/tui/test_history_picker.py -v`
Expected: PASS — both tests.

- [ ] **Step 5: Run full suite**

Run: `uv run pytest`
Expected: 195 passing.

- [ ] **Step 6: Commit**

```bash
git add src/finab/tui/widgets/history_picker.py src/finab/tui/styles.tcss tests/tui/test_history_picker.py
git commit -m "feat(tui): HistoryPickerModal for repeat-from-history"
```

---

## Task 13: SyncScreen keybindings — wire c/s/r to modals

Bind `c`, `s`, `r` on `SyncScreen` to push the three modals. The modal's dismiss-result callback calls the corresponding `engine.apply_*` method, refreshes the pending list row, and advances the cursor.

**Files:**
- Modify: `src/finab/tui/screens/sync.py`
- Modify: `tests/tui/test_sync_screen.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_sync_screen.py`:

```python
@pytest.mark.asyncio
async def test_pressing_c_opens_category_picker(tmp_path):
    """Pressing 'c' on a pending candidate pushes CategoryPickerModal."""
    from datetime import date as date_cls
    from finab.engine.sync import Candidate
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.category_picker import CategoryPickerModal

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-8421,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # The pending candidate is selected (status=pending due to current-month + merchant).
        await pilot.press("c")
        await pilot.pause()
        # A CategoryPickerModal should be on the screen stack.
        assert isinstance(app.screen, CategoryPickerModal)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/tui/test_sync_screen.py::test_pressing_c_opens_category_picker -v`
Expected: FAIL — no `c` binding on the screen yet.

- [ ] **Step 3: Add keybindings + action methods to SyncScreen**

Edit `src/finab/tui/screens/sync.py`. The container needs to handle key actions. Containers can have `BINDINGS` like screens — add them at the class level:

```python
from finab.tui.widgets.category_picker import CategoryPickerModal
from finab.tui.widgets.split_editor import SplitEditorModal
from finab.tui.widgets.history_picker import HistoryPickerModal


class SyncScreen(Container):
    """The Sync screen container — embedded in FinabApp's ContentSwitcher."""

    BINDINGS = [
        ("c", "category", "Category"),
        ("s", "split", "Split"),
        ("r", "history", "Repeat history"),
    ]

    # ... existing __init__, compose, set_candidates, bind_data ...

    def _current_candidate(self):
        pl = self.query_one("#sync-pending", PendingList)
        return pl.current_candidate()

    def action_category(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant = None
        if getattr(c.txn, "merchant_id", None):
            merchant = self._store.merchant_by_finwise_id(c.txn.merchant_id)
        used = (merchant or {}).get("categories_used") or {}
        alias = (merchant or {}).get("alias") or "?"
        modal = CategoryPickerModal(
            categories=self._engine._ynab_categories,
            used_categories=used,
            merchant_alias=alias,
        )

        def _on_picked(category_id):
            if category_id is None:
                return
            self._engine.apply_category(c.id, category_id=category_id)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_split(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant = None
        if getattr(c.txn, "merchant_id", None):
            merchant = self._store.merchant_by_finwise_id(c.txn.merchant_id)
        used = (merchant or {}).get("categories_used") or {}
        alias = (merchant or {}).get("alias") or "?"
        modal = SplitEditorModal(
            txn_amount=c.txn.amount,
            categories=self._engine._ynab_categories,
            used_categories=used,
            merchant_alias=alias,
        )

        def _on_done(splits):
            if splits is None:
                return
            self._engine.apply_split(c.id, splits=splits)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_done)

    def action_history(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant_id = getattr(c.txn, "merchant_id", None)
        if not merchant_id:
            return
        merchant = self._store.merchant_by_finwise_id(merchant_id)
        if not merchant:
            return
        processings = merchant.get("processings") or {}
        if not processings:
            return
        modal = HistoryPickerModal(processings=processings, txn_amount=c.txn.amount)

        def _on_picked(result):
            if result is None:
                return
            _amount_key, entry = result
            from finab.engine.sync import _apply_processing_to_txn
            _apply_processing_to_txn(entry, c.txn)
            c.status = "decided"
            # Synthesize prior_state so undo works (snapshot what _apply just overwrote — best effort).
            # For Plan 2 we don't have a pre-apply snapshot here; the engine's apply_* methods
            # snapshot inside. Document the gap and accept it for Plan 2:
            # repeat-from-history decisions aren't undoable until Plan 3.
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)

    def _refresh_after_decision(self, candidate_id: str) -> None:
        """After an engine.apply_*, rebuild the row and move cursor down one."""
        pl = self.query_one("#sync-pending", PendingList)
        pl.refresh_row(candidate_id)
        # Move cursor to next pending row if there is one.
        next_idx = pl.index + 1 if pl.index is not None else 0
        if next_idx < len(pl.candidates):
            pl.index = next_idx
        # Refresh the detail card.
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)
```

A note on the history-picker undo gap: writing this prompt now, the gap is that `engine.apply_history` doesn't exist — the history picker bypasses the engine and mutates `c.txn` directly. This means undo won't work for those decisions. Document the gap in code and don't fight it for Plan 2. A future task can add `SyncEngine.apply_history(candidate_id, entry)` that snapshots prior state properly.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/tui/test_sync_screen.py::test_pressing_c_opens_category_picker -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: 196 passing.

- [ ] **Step 6: Commit**

```bash
git add src/finab/tui/screens/sync.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): SyncScreen keybindings — open c/s/r modals"
```

---

## Task 14: Flush + undo + footer hints

Add `f` (flush all decided+auto to YNAB), `u` (undo current row), `q` (quit with flush prompt). Add a footer showing key hints.

**Files:**
- Modify: `src/finab/tui/screens/sync.py`
- Modify: `src/finab/tui/app.py` (add Footer)
- Modify: `tests/tui/test_sync_screen.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/tui/test_sync_screen.py`:

```python
@pytest.mark.asyncio
async def test_u_undoes_the_current_row(tmp_path):
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-8421,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # Decide first.
        sync_screen._engine.apply_category(sync_screen._engine.candidates[0].id, category_id="cat-x")
        await pilot.pause()
        # Press u to undo.
        await pilot.press("u")
        await pilot.pause()
        c = sync_screen._engine.candidates[0]
        assert c.status == "pending"


@pytest.mark.asyncio
async def test_f_calls_flush(tmp_path):
    """Pressing f calls engine.flush with a stub ynab_client provided to the screen."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen

    flushed = {"called": False}

    class _StubYnab:
        def create_transactions(self, budget_id, txns): flushed["called"] = True
        def update_transactions(self, budget_id, txns): pass

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-1",
        amount=-8421,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )

    app = FinabApp(ynab_client=_StubYnab(), budget_id="bid")
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(
            loaded=LoadedData(fw_transactions=[txn]),
            store=store,
            tx_store=tx_store,
        )
        await pilot.pause()
        # Decide first.
        sync_screen._engine.apply_category(sync_screen._engine.candidates[0].id, category_id="cat-x")
        await pilot.pause()
        # Press f to flush.
        await pilot.press("f")
        await pilot.pause()
        assert flushed["called"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/tui/test_sync_screen.py::test_u_undoes_the_current_row tests/tui/test_sync_screen.py::test_f_calls_flush -v`
Expected: FAIL — `u` / `f` not bound yet.

- [ ] **Step 3: Add u/f/q bindings and actions to SyncScreen**

Edit `src/finab/tui/screens/sync.py`. Update BINDINGS:

```python
    BINDINGS = [
        ("c", "category", "Category"),
        ("s", "split", "Split"),
        ("r", "history", "Repeat history"),
        ("u", "undo", "Undo"),
        ("f", "flush", "Flush"),
    ]
```

Add action methods:

```python
    def action_undo(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        try:
            self._engine.undo(c.id)
        except ValueError:
            # Not a decided candidate — no-op, maybe bell.
            self.app.bell()
            return
        self._refresh_after_decision(c.id)

    def action_flush(self) -> None:
        if self._engine is None:
            return
        ynab_client = getattr(self.app, "_ynab_client", None)
        budget_id = getattr(self.app, "_budget_id", None)
        if ynab_client is None or not budget_id:
            self.app.bell()
            return
        try:
            self._engine.flush(ynab_client, budget_id)
        except Exception:
            # Plan 2: bell on failure. Plan 3 should surface the error.
            self.app.bell()
            return
        # Refresh all rows — many candidates may have moved to 'flushed'.
        pl = self.query_one("#sync-pending", PendingList)
        for c in pl.candidates:
            pl.refresh_row(c.id)
```

- [ ] **Step 4: Add a Footer to FinabApp**

Edit `src/finab/tui/app.py`. Import Footer and yield it at the bottom of `compose`:

```python
from textual.widgets import Footer, Label, ListItem, ListView

# ... in compose():
    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(...)
            with ContentSwitcher(...):
                ...
        yield Footer()
```

The Footer auto-populates from each focused widget/screen's BINDINGS. Since SyncScreen has its `c / s / r / u / f` bindings, those show up when Sync is focused.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/tui/test_sync_screen.py -v`
Expected: all tests pass.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: 198 passing.

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/screens/sync.py src/finab/tui/app.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): undo/flush bindings + Footer with key hints"
```

---

## Task 15: Final smoke test + verification

Verify the whole TUI works end-to-end against a stubbed-engine scenario, run the full test suite, do a manual launch.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -v`
Expected: 198 passing total. Zero failures, zero errors. No new warnings about deprecated Textual APIs.

- [ ] **Step 2: Boundary check**

Run: `grep -rn "input(\|^from textual\|^import textual" src/finab/engine/`
Expected: zero matches. The engine subpackage must stay Textual-free.

- [ ] **Step 3: TUI smoke via run_test**

Run: `uv run pytest tests/tui/ -v --tb=short`
Expected: every TUI test passes. Look at the test names — should include app boot, sidebar nav, sync screen layout, modals, key bindings.

- [ ] **Step 4: Manual launch (if a terminal is available)**

Run: `FINAB_TUI=1 uv run finab` and observe:
- Sidebar appears with 5 items
- Pressing `down` moves between sidebar entries; content area switches
- Pressing `c` on a pending sync row opens the category picker
- Pressing `s` opens the split editor
- Pressing `r` (with a merchant that has processings) opens history picker
- Pressing `q` quits cleanly

If you can't launch interactively, skip this step and note it. The pilot-driven tests cover the same paths.

- [ ] **Step 5: Inspect module structure**

Run: `find src/finab/tui tests/tui -type f -name '*.py' -o -name '*.tcss' | sort`
Expected output (file list):

```
src/finab/tui/__init__.py
src/finab/tui/app.py
src/finab/tui/data_loader.py
src/finab/tui/screens/__init__.py
src/finab/tui/screens/placeholder.py
src/finab/tui/screens/sync.py
src/finab/tui/styles.tcss
src/finab/tui/widgets/__init__.py
src/finab/tui/widgets/category_picker.py
src/finab/tui/widgets/history_picker.py
src/finab/tui/widgets/pending_list.py
src/finab/tui/widgets/split_editor.py
src/finab/tui/widgets/transaction_card.py
tests/tui/__init__.py
tests/tui/test_app.py
tests/tui/test_category_picker.py
tests/tui/test_data_loader.py
tests/tui/test_history_picker.py
tests/tui/test_pending_list.py
tests/tui/test_split_editor.py
tests/tui/test_sync_screen.py
```

- [ ] **Step 6: Commit log review**

Run: `git log --oneline HEAD~15..HEAD`
Expected: 14-15 commits for Plan 2 (one per task), with consistent `feat(tui):` / `deps:` prefix.

No code changes in this task — verification only.

---

## Self-Review

**Spec coverage:**
- Spec §Sync screen layout — Task 6, 7, 8 (master/detail, status glyphs, transaction card).
- Spec §Status glyphs — Task 7 (`_GLYPHS` map covers pending/decided/auto-variants/flushed). Missing: `⚠` (FW-transfer-not-linked) per holistic review of Plan 1 — but this is an engine-side gap not a TUI gap. Plan 3 should add `Candidate.warnings`.
- Spec §Candidate state machine — already enforced by the engine from Plan 1; TUI consumes it.
- Spec §Keybindings (j/k/Enter/c/s/r/t/u/f/q/g/G/?) — Plan 2 covers `c`, `s`, `r`, `u`, `f`. `t` (force transfer), `q` (quit with flush prompt), `Enter` (repeat closest), `g/G/?` — defer to Plan 3 polish.
- Spec §Category picker modal — Task 10.
- Spec §Split editor modal — Task 11 (with command-line UX caveat; the data shape and invariant match spec).
- Spec §Repeat-from-history modal — Task 12.
- Spec §Other screens — placeholder only; Plan 3.
- Spec §Data flow & persistence — Task 5 (`load_all`) + Task 9 (engine construction).
- Spec §Error handling fetch failures — `LoadedData.error` captured in Task 5; surfaced as a banner is deferred to Plan 3.
- Spec §Error handling flush failures — Task 14 catches with bell; surfacing modal is Plan 3.
- Spec §Ctrl+C with confirm — deferred to Plan 3. Plan 2 just exits.
- Spec §Migration plan step 4 (Textual scaffolding) — Tasks 1-5.
- Spec §Migration plan step 5 (per-screen) — only Sync. Other screens are Plan 3.

Known gaps that are intentionally deferred to Plan 3:
- Force-transfer key (`t`)
- Enter-to-repeat-closest
- Help overlay (`?`)
- g / G navigation
- ⚠ warning state
- Banners for fetch failures
- Modal for flush failures
- Ctrl+C confirm prompt
- HistoryPicker apply via engine (so undo works for history decisions)

These are listed here so Plan 3 can pick them up. None block Plan 2 from shipping a usable Sync screen.

**Placeholder scan:**
- No "TBD" / "implement later" steps. The "command-line UX in split editor" choice is documented as a deliberate Plan 2 simplification, not a placeholder.

**Type consistency:**
- `Candidate.id: str` everywhere.
- `CategoryPickerModal[str | None]`, `SplitEditorModal[list | None]`, `HistoryPickerModal[tuple[str, dict] | None]` — typed dismiss results.
- `engine.apply_category(candidate_id, *, category_id, memo=None)` — matches what the picker callback calls.
- `engine.apply_split(candidate_id, *, splits, memo=None)` — `splits` is `list[dict]` with keys `amount`, `category_id`, `memo`. The split editor produces exactly this shape.
- `engine.undo(candidate_id)` — raises ValueError on non-decided. Task 14's `action_undo` catches and bells.
- `engine.flush(ynab_client, budget_id)` — Task 14 reads `ynab_client` and `budget_id` from `self.app`.

**Files I expect to NOT touch** (sanity check): `src/finab/engine/*` (frozen from Plan 1), `src/finab/store.py`, `src/finab/transactions.py` (no engine changes; CLI prompt code stays put), `tests/test_*` (pre-existing CLI tests untouched). Plan 2 is purely additive in `src/finab/tui/` + `tests/tui/` + small entrypoint changes in `src/finab/main.py`.

---
