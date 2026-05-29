"""YnabPayeePicker — fuzzy-search modal over fetched YNAB payees.

Used during the Merchants screen's mapping flow when the user wants to
link a new FW merchant to an existing YNAB payee.

Filters out deleted payees and (by default) transfer payees, which are
internal YNAB constructs for own-account transfers — those don't make
sense as merchant linkages.

Dismisses with the chosen payee's id (str), or None on cancel.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class YnabPayeePicker(ModalScreen[Optional[str]]):
    """Returns the chosen payee's id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        ynab_payees: list,
        title: str = "Pick a YNAB payee",
    ):
        super().__init__()
        self._all = [
            p for p in ynab_payees
            if not getattr(p, "deleted", False)
            and not getattr(p, "transfer_account_id", None)
        ]
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="ynab-payee-picker-dialog"):
            yield Static(self._title, id="ynab-payee-picker-title")
            yield Input(placeholder="filter…", id="ynab-payee-picker-filter")
            yield OptionList(id="ynab-payee-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#ynab-payee-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#ynab-payee-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [p for p in self._all if not f or f in str(p.name).lower()]
        for p in rows:
            ol.add_option(Option(p.name, id=str(p.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "ynab-payee-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#ynab-payee-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
