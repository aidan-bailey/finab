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


@pytest.mark.asyncio
async def test_transaction_card_shows_empty_state():
    """No candidates → card shows empty-state text."""
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        card = app.query_one("#sync-detail")
        # When no candidate is selected, card content is empty-state.
        # Textual 8.x: read via .content; fallback to .renderable.
        text = str(getattr(card, "content", None) or getattr(card, "renderable", ""))
        assert "select a transaction" in text.lower() or "no transaction" in text.lower()


@pytest.mark.asyncio
async def test_transaction_card_renders_candidate():
    """Card renders the candidate's fields after one is selected."""
    from datetime import date as _date
    from finab.engine.sync import Candidate
    from finab.tui.app import FinabApp

    class FakeTxn:
        amount = -84210  # milliunits: -84.21
        memo = "COSTCO WHSE #1234"
        date = _date(2026, 5, 22)
        category_id = None
        subtransactions = []

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one("#screen-sync")
        sync_screen.set_candidates(
            [Candidate(id="abc", txn=FakeTxn(), status="pending")],
            alias_of=lambda c: "Costco",
        )
        await pilot.pause()
        card = app.query_one("#sync-detail")
        card_text = str(getattr(card, "content", None) or getattr(card, "renderable", ""))
        assert "Costco" in card_text
        assert "-84.21" in card_text
        assert "2026-05-22" in card_text
        assert "COSTCO WHSE" in card_text
