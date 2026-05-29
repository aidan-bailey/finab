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


@pytest.mark.asyncio
async def test_check_action_hides_sync_bindings_when_settings_active():
    """When the Settings screen is active, sync-only bindings should be hidden."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Switch to settings.
        app._active_screen = "screen-settings"
        await pilot.pause()
        # Sync bindings: hidden.
        assert app.check_action("sync_category", ()) is False
        assert app.check_action("sync_flush", ()) is False
        assert app.check_action("sync_repeat_closest", ()) is False
        # Quit and help: still visible.
        assert app.check_action("quit_with_confirm", ()) is True
        assert app.check_action("show_help", ()) is True


@pytest.mark.asyncio
async def test_check_action_shows_sync_bindings_when_sync_active():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._active_screen = "screen-sync"
        await pilot.pause()
        assert app.check_action("sync_category", ()) is True
        assert app.check_action("sync_flush", ()) is True
        # Memory bindings: hidden.
        assert app.check_action("memory_delete", ()) is False
        assert app.check_action("memory_reset", ()) is False


@pytest.mark.asyncio
async def test_check_action_rename_visible_on_both_accounts_and_merchants():
    """accounts_rename dispatches to both AccountsScreen and MerchantsScreen
    (legacy name from Plan 3). It should be visible on either."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app._active_screen = "screen-accounts"
        await pilot.pause()
        assert app.check_action("accounts_rename", ()) is True
        assert app.check_action("accounts_toggle_ignore", ()) is True  # accounts-only
        app._active_screen = "screen-merchants"
        await pilot.pause()
        assert app.check_action("accounts_rename", ()) is True
        # toggle_ignore is accounts-only — hidden on merchants.
        assert app.check_action("accounts_toggle_ignore", ()) is False


@pytest.mark.asyncio
async def test_sidebar_navigation_updates_active_screen():
    """When the user moves the sidebar cursor, _active_screen tracks the new screen."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Initial state: sync is the default.
        assert app._active_screen == "screen-sync"
        # Move sidebar cursor down to Accounts.
        sidebar = app.query_one("#sidebar")
        sidebar.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app._active_screen == "screen-accounts"


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


