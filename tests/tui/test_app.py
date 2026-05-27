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


def test_main_falls_through_to_cli_when_flag_unset(monkeypatch, capsys):
    """When FINAB_TUI is unset, finab.main.main() does NOT touch FinabApp."""
    monkeypatch.delenv("FINAB_TUI", raising=False)

    import finab.tui.app as tui_app_mod
    class ExplodingApp:
        def __init__(self):
            raise AssertionError("FinabApp should not be constructed when FINAB_TUI is unset")
    monkeypatch.setattr(tui_app_mod, "FinabApp", ExplodingApp)

    # The existing CLI flow tries to initialize YNAB client. Patch it to short-circuit.
    import finab.main as main_mod
    class FakeYnabClient:
        def __init__(self): raise RuntimeError("stop here — flag was unset, CLI path taken")
    monkeypatch.setattr(main_mod, "YNABClient", FakeYnabClient)

    from finab.main import main
    main()  # Should print an error from FakeYnabClient and return without crashing
    # The important thing is that ExplodingApp was NEVER constructed.
    # No assertion needed beyond getting here without AssertionError.
