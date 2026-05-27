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
