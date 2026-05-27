"""AliasInputModal — single-line input prompt.

Dismisses with the entered string on Enter, or None on Escape.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class AliasInputModal(ModalScreen[Optional[str]]):
    """Returns the entered alias string, or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, prompt: str, default: str = ""):
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="alias-input-dialog"):
            yield Static(self._prompt, id="alias-input-prompt")
            yield Input(value=self._default, id="alias-input-field")

    def on_mount(self) -> None:
        self.query_one("#alias-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = (event.value or "").strip()
        if not value:
            self.dismiss(None)
            return
        self.dismiss(value)
