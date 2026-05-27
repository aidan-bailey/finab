"""AccountLinkPicker — fuzzy-search modal over the store's accounts.

Dismisses with the chosen account's value according to `value_kind`:
  - "transfer_payee_id" (default): for Sync's force-transfer
  - "account_internal_id": for relinking workflows
  - "ynab_account_id": for tasks needing the YNAB-side id directly
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class AccountLinkPicker(ModalScreen[Optional[str]]):
    """Returns the chosen account's id (kind set by value_kind), or None."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        store,
        title: str = "Pick an account",
        value_kind: str = "transfer_payee_id",
    ):
        super().__init__()
        self._store = store
        self._title = title
        self._value_kind = value_kind
        self._filter_text = ""
        self._all = self._collect()

    def _collect(self) -> list:
        rows = []
        for a in self._store.accounts():
            ynab = a.get("ynab") or {}
            if self._value_kind == "transfer_payee_id":
                value = ynab.get("transfer_payee_id")
                if not value:
                    continue
            elif self._value_kind == "account_internal_id":
                value = a.get("id")
            elif self._value_kind == "ynab_account_id":
                value = ynab.get("id")
                if not value:
                    continue
            else:
                continue
            rows.append({"alias": a["alias"], "value": value})
        return rows

    def compose(self) -> ComposeResult:
        with Vertical(id="account-picker-dialog"):
            yield Static(self._title, id="account-picker-title")
            yield Input(placeholder="filter…", id="account-picker-filter")
            yield OptionList(id="account-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#account-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#account-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [a for a in self._all if not f or f in a["alias"].lower()]
        for a in rows:
            ol.add_option(Option(a["alias"], id=a["value"]))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "account-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#account-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
