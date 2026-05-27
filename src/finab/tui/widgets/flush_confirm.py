"""FlushConfirmModal — three-way prompt on quit with pending decisions.

Dismisses with one of:
  "flush"  — yes, push pending then exit
  "skip"   — no, exit without flushing
  "cancel" — never mind, stay in the app
"""
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


Result = Literal["flush", "skip", "cancel"]


class FlushConfirmModal(ModalScreen[Result]):
    """Three-way confirm before quitting with un-flushed decisions."""

    BINDINGS = [
        ("y", "dismiss('flush')", "Yes"),
        ("n", "dismiss('skip')", "No"),
        ("escape", "dismiss('cancel')", "Cancel"),
    ]

    def __init__(self, *, pending_count: int):
        super().__init__()
        self._pending_count = pending_count

    def compose(self) -> ComposeResult:
        with Vertical(id="flush-confirm-dialog"):
            yield Static(
                f"You have {self._pending_count} pending transaction(s) "
                f"that haven't been pushed to YNAB.",
                id="flush-confirm-message",
            )
            yield Static(
                "  y — Flush them and quit\n"
                "  n — Quit without flushing (they'll re-appear next sync)\n"
                "  Esc — Cancel, stay in the app",
                id="flush-confirm-options",
            )
