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
