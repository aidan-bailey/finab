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
