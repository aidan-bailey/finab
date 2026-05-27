"""MerchantsScreen — sidebar entry #3.

Lists merchants with state glyph + alias + linked-to.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


def _merchant_glyph(m: dict) -> str:
    ynab = m.get("ynab") or {}
    if ynab.get("transfer_account_id"):
        return "→"
    if ynab.get("id"):
        return "✓"
    return "!"


class MerchantsScreen(Container):
    """Sidebar entry #3 — browse and edit merchant mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None

    def compose(self) -> ComposeResult:
        yield ListView(id="merchants-list")

    def bind_data(self, *, store) -> None:
        self._store = store
        self.refresh_rows()

    def refresh_rows(self) -> None:
        lv = self.query_one("#merchants-list", ListView)
        lv.clear()
        if self._store is None:
            return
        # Don't set explicit IDs on ListItems — Textual retains them in the
        # node registry across clear() calls until the next event tick, which
        # causes DuplicateIds on the second refresh. (Lesson from Task 7.)
        for m in self._store.merchants():
            glyph = _merchant_glyph(m)
            alias = m.get("alias", "?")
            ynab = m.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            link_kind = "transfer payee" if ynab.get("transfer_account_id") else ("payee" if ynab.get("id") else "")
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<26.26}  {link_kind}"
            lv.append(ListItem(Label(text)))

    def row_count(self) -> int:
        return len(list(self._store.merchants())) if self._store else 0

    def set_cursor(self, index: int) -> None:
        self.query_one("#merchants-list", ListView).index = index

    def _current_merchant(self) -> Optional[dict]:
        lv = self.query_one("#merchants-list", ListView)
        idx = lv.index
        if idx is None or self._store is None:
            return None
        merchants = list(self._store.merchants())
        if 0 <= idx < len(merchants):
            return merchants[idx]
        return None

    def action_rename(self) -> None:
        m = self._current_merchant()
        if m is None or self._store is None:
            return
        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Rename '{m['alias']}':",
            default=m.get("alias", ""),
        )

        def _on_done(new_alias):
            if new_alias is None or new_alias == m.get("alias"):
                return
            self._store.set_merchant_alias(m["id"], new_alias)
            self.refresh_rows()

        self.app.push_screen(modal, callback=_on_done)

    def action_relink(self) -> None:
        # Plan 3 scope: bell. Plan 4 (or follow-up) adds proper picker
        # over YNAB payees + own accounts.
        self.app.bell()
