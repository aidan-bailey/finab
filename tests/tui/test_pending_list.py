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
    txn._alias = alias  # synthetic — PendingList reads via alias_of callable
    return Candidate(id=f"cid-{alias}-{amount}", txn=txn, status=status, auto_reason=auto_reason)


@pytest.mark.asyncio
async def test_pending_list_renders_candidates():
    """A PendingList given 3 candidates renders 3 rows with the expected glyphs."""
    from textual.app import App
    from finab.tui.widgets.pending_list import PendingList

    candidates = [
        _make_candidate(alias="Amazon", amount=-23990, status="decided"),
        _make_candidate(alias="Costco", amount=-84210, status="pending"),
        _make_candidate(alias="Salary", amount=1500000, status="auto", auto_reason="inflow"),
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


@pytest.mark.asyncio
async def test_pending_list_warning_glyph():
    """A candidate with warnings gets the ⚠ glyph regardless of status."""
    from textual.app import App
    from finab.tui.widgets.pending_list import PendingList

    candidates = [_make_candidate(alias="Costco", amount=-50000, status="pending")]
    candidates[0].warnings = ["fake warning"]

    class _Host(App):
        def compose(self):
            yield PendingList(candidates=candidates, alias_of=lambda c: c.txn._alias, id="pl")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        pl = app.query_one("#pl", PendingList)
        rows = pl.row_glyphs_and_text()
        assert rows[0][0] == "⚠"  # warning glyph wins over status
