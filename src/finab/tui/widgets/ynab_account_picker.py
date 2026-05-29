"""YnabAccountPicker — fuzzy-search modal over *fetched* YNAB accounts.

Used during the Accounts screen's mapping flow when the user wants to
link a new FW account to an existing YNAB account (rather than create
a new one).

Dismisses with the chosen YNAB account's id (str), or None on cancel.

Different from AccountLinkPicker (which scans the store's already-mapped
accounts). This picker takes the raw YNAB-side list fetched at boot.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class YnabAccountPicker(ModalScreen[Optional[str]]):
    """Returns the chosen YNAB account's id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        ynab_accounts: list,
        title: str = "Pick a YNAB account",
    ):
        super().__init__()
        # Filter out deleted/closed accounts up front.
        self._all = [
            a for a in ynab_accounts
            if not getattr(a, "deleted", False)
            and not getattr(a, "closed", False)
        ]
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="ynab-account-picker-dialog"):
            yield Static(self._title, id="ynab-account-picker-title")
            yield Input(placeholder="filter…", id="ynab-account-picker-filter")
            yield OptionList(id="ynab-account-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#ynab-account-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#ynab-account-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [a for a in self._all if not f or f in str(a.name).lower()]
        for a in rows:
            label = f"{a.name}  ({a.type})"
            ol.add_option(Option(label, id=str(a.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ynab-account-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter inside the input picks the highlighted row."""
        ol = self.query_one("#ynab-account-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
