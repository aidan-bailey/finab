"""WizardBanner — top-of-app strip shown during first-run setup.

Hidden by default. `show(step, total, text)` renders a step indicator like
`Setup — Step 2/3: map every account, then press n`. `hide()` collapses it
once the wizard finishes.
"""
from textual.widgets import Static


class WizardBanner(Static):
    """Mounted at the top of FinabApp; empty/hidden unless the wizard is active."""

    DEFAULT_CSS = """
    WizardBanner {
        background: $secondary 40%;
        color: $text;
        padding: 0 2;
        height: auto;
        display: none;
    }

    WizardBanner.active {
        display: block;
    }
    """

    def __init__(self, *, id: str = None):
        super().__init__("", id=id)

    def show(self, step: int, total: int, text: str) -> None:
        self.update(f"Setup — Step {step}/{total}: {text}")
        self.add_class("active")

    def hide(self) -> None:
        self.update("")
        self.remove_class("active")
