"""Smoke tests for FinabApp.

Each test uses Textual's Pilot to interact with the app headlessly —
no terminal required. The conftest sandbox (which re-points state
file paths) applies here too.
"""
import pytest


@pytest.mark.asyncio
async def test_app_boots_and_shows_sidebar():
    """The bare app starts and the sidebar is on screen."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        assert sidebar is not None


@pytest.mark.asyncio
async def test_app_exits_on_q():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        # After q, app should be exited. run_test context will complete.
    # If we got here without timing out, the app exited cleanly.


@pytest.mark.asyncio
async def test_sidebar_has_five_screens():
    """Sidebar lists Sync, Accounts, Merchants, Memory, Settings."""
    from finab.tui.app import FinabApp
    from textual.widgets import ListItem, Label
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        items = app.query(ListItem)
        labels_str = [str(item.query_one(Label).content) for item in items]
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


def test_main_launches_tui_when_flag_set(monkeypatch):
    """LEGACY: FINAB_TUI=1 still triggers TUI (now the default anyway)."""
    monkeypatch.setenv("FINAB_TUI", "1")

    launched = {"called": False}

    class FakeApp:
        def __init__(self, **kwargs): pass
        def run(self):
            launched["called"] = True

    import finab.tui.app as tui_app_mod
    monkeypatch.setattr(tui_app_mod, "FinabApp", FakeApp)

    from finab.main import main
    main()

    assert launched["called"] is True


def test_main_runs_tui_by_default(monkeypatch):
    """With no flag and no env var, main() launches FinabApp."""
    monkeypatch.delenv("FINAB_TUI", raising=False)
    launched = {"count": 0}

    class FakeApp:
        def __init__(self, **kwargs): pass
        def run(self): launched["count"] += 1

    import finab.tui.app as tui_app_mod
    monkeypatch.setattr(tui_app_mod, "FinabApp", FakeApp)
    monkeypatch.setattr("sys.argv", ["finab"])

    from finab.main import main
    main()
    assert launched["count"] == 1


def test_main_classic_flag_runs_cli(monkeypatch):
    """--classic falls through to the old prompt flow."""
    monkeypatch.delenv("FINAB_TUI", raising=False)
    monkeypatch.setattr("sys.argv", ["finab", "--classic"])

    import finab.tui.app as tui_app_mod
    class ExplodingApp:
        def __init__(self, **kwargs):
            raise AssertionError("FinabApp should not be constructed when --classic is passed")
    monkeypatch.setattr(tui_app_mod, "FinabApp", ExplodingApp)

    import finab.main as main_mod
    class FakeYnabClient:
        def __init__(self): raise RuntimeError("stop here")
    monkeypatch.setattr(main_mod, "YNABClient", FakeYnabClient)

    from finab.main import main
    main()  # should print error and return without crashing
