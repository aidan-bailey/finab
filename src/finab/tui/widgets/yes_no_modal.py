"""YesNoModal — minimal 2-button confirm.

Dismisses with True on `y`, False on `n`, None on Escape. When
`enter_confirms=True`, Enter is also accepted as Yes (used by the merchant
create-payee prompt so a default-named merchant links in one keystroke).

Used by the Accounts/Merchants mapping flow for the "no existing
target — create new?" prompt.
"""
from typing import Optional

from textual import events
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

    def __init__(self, *, message: str, enter_confirms: bool = False):
        super().__init__()
        self._message = message
        self._enter_confirms = enter_confirms

    def compose(self) -> ComposeResult:
        with Vertical(id="yes-no-dialog"):
            yield Static(self._message, id="yes-no-message")
            hint = (
                "  Enter/y — Yes    n — No    Esc — Cancel"
                if self._enter_confirms
                else "  y — Yes    n — No    Esc — Cancel"
            )
            yield Static(hint, id="yes-no-hints")

    def on_key(self, event: events.Key) -> None:
        # Opt-in: accept Enter as Yes. Handled here (not as a BINDING) so the
        # modal claims the key before it bubbles to the app's global `enter`
        # binding (which is non-priority), and so the default behaviour — no
        # Enter handling at all — is preserved when the flag is off.
        if self._enter_confirms and event.key == "enter":
            event.stop()
            self.dismiss(True)
