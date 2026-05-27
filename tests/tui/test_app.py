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
        # The hello widget should be there.
        from textual.widgets import Static
        statics = app.query(Static)
        texts = [str(s.content) for s in statics]
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
