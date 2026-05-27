"""ErrorBanner — mounted at the top of FinabApp; shows fetch errors.

When empty, hidden (no border, no padding). When `show(text)` is called,
displays a red bordered banner.
"""
from textual.widgets import Static


class ErrorBanner(Static):
    """Mounted at the top of the FinabApp. Empty by default."""

    DEFAULT_CSS = """
    ErrorBanner {
        background: $error 30%;
        color: $text;
        padding: 0 2;
        height: auto;
        display: none;
    }

    ErrorBanner.has-error {
        display: block;
    }
    """

    def __init__(self, *, id: str = None):
        super().__init__("", id=id)

    def show(self, message: str) -> None:
        self.update(message)
        self.add_class("has-error")

    def hide(self) -> None:
        self.update("")
        self.remove_class("has-error")
