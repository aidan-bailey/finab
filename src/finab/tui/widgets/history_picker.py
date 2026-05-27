"""HistoryPickerModal — pick a prior processing for the current merchant.

Dismisses with (amount_key, entry) tuple, or None on cancel.

The "closest to current amount" row is pre-highlighted on mount so a
plain Enter picks it.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option


def _closest_key(processings: dict, txn_amount: int) -> Optional[str]:
    """Return the processings key with amount closest to txn_amount, or
    None if processings is empty."""
    best = None
    best_diff = None
    for k in processings.keys():
        try:
            k_amt = int(k)
        except (TypeError, ValueError):
            continue
        diff = abs(k_amt - txn_amount)
        if best_diff is None or diff < best_diff:
            best = k
            best_diff = diff
    return best


class HistoryPickerModal(ModalScreen[Optional[tuple[str, dict]]]):
    """Returns (amount_key, entry_dict) tuple or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, processings: dict, txn_amount: int):
        super().__init__()
        self._processings = dict(processings)
        self._txn_amount = txn_amount

    def compose(self) -> ComposeResult:
        with Vertical(id="history-dialog"):
            yield Static("Prior categorizations", id="history-title")
            yield OptionList(id="history-options")

    def on_mount(self) -> None:
        ol = self.query_one("#history-options", OptionList)
        closest = _closest_key(self._processings, self._txn_amount)
        keys = list(self._processings.keys())
        for k in keys:
            entry = self._processings[k]
            try:
                amt = int(k) / 1000.0
                amt_str = f"{amt:>10.2f}"
            except (TypeError, ValueError):
                amt_str = f"{k:>10}"
            splits = entry.get("splits", []) or []
            if len(splits) == 1:
                label = f"{amt_str}   {splits[0].get('category_id', '?')}"
            else:
                label = f"{amt_str}   split ({len(splits)} categories)"
            if k == closest:
                label += "  (closest)"
            ol.add_option(Option(label, id=k))
        if closest is not None and keys:
            ol.highlighted = keys.index(closest)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        k = event.option.id
        if k is None or k not in self._processings:
            self.dismiss(None)
            return
        self.dismiss((k, self._processings[k]))
