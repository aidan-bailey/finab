"""Pilot-driven tests for the Sync screen."""
import pytest


@pytest.mark.asyncio
async def test_sync_screen_has_two_panes():
    """SyncScreen has a pending-list pane (left) and a detail pane (right)."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        pending_pane = app.query_one("#sync-pending")
        detail_pane = app.query_one("#sync-detail")
        assert pending_pane is not None
        assert detail_pane is not None
