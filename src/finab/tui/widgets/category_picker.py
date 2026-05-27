"""CategoryPickerModal — fuzzy-search category picker.

Dismisses with the selected category_id (str), or None if cancelled.

Ranking:
  1. Merchant's used categories (sorted by frequency descending).
  2. All other non-hidden, non-deleted categories (alphabetical).

Filtering: substring match (case-insensitive) on category name.
"""
from typing import Mapping

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class CategoryPickerModal(ModalScreen[str | None]):
    """Modal that returns a category_id (str) or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(
        self,
        *,
        categories: list,
        used_categories: Mapping[str, int],
        merchant_alias: str,
    ):
        super().__init__()
        # Filter out hidden/deleted up front.
        self._all = [
            c for c in categories
            if not getattr(c, "hidden", False) and not getattr(c, "deleted", False)
        ]
        self._used = dict(used_categories)
        self._merchant_alias = merchant_alias
        self._filter_text = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-dialog"):
            yield Static(f"Pick category for '{self._merchant_alias}'", id="picker-title")
            yield Input(placeholder="filter…", id="picker-filter")
            yield OptionList(id="picker-options")

    def on_mount(self) -> None:
        self._refresh_options()
        self.query_one("#picker-filter", Input).focus()

    def _ranked_options(self) -> list[tuple[str, str]]:
        """Return [(category_id, display_text), ...] in ranked order."""
        f = self._filter_text.lower()
        used_matches = []
        other_matches = []
        for c in self._all:
            name = str(getattr(c, "name", ""))
            if f and f not in name.lower():
                continue
            cid = str(c.id)
            if cid in self._used:
                label = f"{name}  ({self._used[cid]}x for {self._merchant_alias})"
                used_matches.append((self._used[cid], cid, label))
            else:
                other_matches.append((cid, name))
        used_matches.sort(key=lambda t: (-t[0], t[2].lower()))
        other_matches.sort(key=lambda t: t[1].lower())
        out = [(cid, label) for _, cid, label in used_matches]
        out.extend(other_matches)
        return out

    def _refresh_options(self) -> None:
        ol = self.query_one("#picker-options", OptionList)
        ol.clear_options()
        for cid, label in self._ranked_options():
            ol.add_option(Option(label, id=cid))
        # Pre-highlight the first option so Enter immediately selects it.
        if ol.option_count > 0:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh_options()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User pressed Enter on a row — dismiss with that category's id."""
        if event.option.id:
            self.dismiss(event.option.id)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Pressing Enter inside the input acts as 'pick the highlighted row'."""
        ol = self.query_one("#picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        if opt.id:
            self.dismiss(opt.id)
