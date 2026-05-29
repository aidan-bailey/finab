"""YesNoModal — minimal 2-button confirm.

Dismisses with True on `y`, False on `n`, None on Escape.

Used by the Accounts/Merchants mapping flow for the "no existing
target — create new?" prompt.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class YesNoModal(ModalScreen[Optional[bool]]):
    """Returns True / False / None (cancel)."""

    BINDINGS = [
        ("y", "dismiss(True)", "Yes"),
        ("n", "dismiss(False)", "No"),
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, message: str):
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="yes-no-dialog"):
            yield Static(self._message, id="yes-no-message")
            yield Static("  y — Yes    n — No    Esc — Cancel", id="yes-no-hints")
