"""BudgetPickerModal — modal over the YNAB budgets (plans) returned by
`ynab_client.get_budgets()`.

Used by the first-run setup wizard to acquire a `budget_id` when none is
configured (e.g. right after `finab --reset`). Mirrors `YnabAccountPicker`:
a title, a filter `Input`, and an `OptionList` of budgets.

Dismisses with the chosen budget id (str), or None on cancel.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class BudgetPickerModal(ModalScreen[Optional[str]]):
    """Returns the chosen YNAB budget id (str), or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        budgets: list,
        title: str = "Select your YNAB budget",
    ):
        super().__init__()
        self._all = list(budgets)
        self._title = title
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="budget-picker-dialog"):
            yield Static(self._title, id="budget-picker-title")
            yield Input(placeholder="filter…", id="budget-picker-filter")
            yield OptionList(id="budget-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#budget-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#budget-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [b for b in self._all if not f or f in str(b.name).lower()]
        for b in rows:
            ol.add_option(Option(str(b.name), id=str(b.id)))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "budget-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter inside the filter picks the highlighted row."""
        ol = self.query_one("#budget-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
