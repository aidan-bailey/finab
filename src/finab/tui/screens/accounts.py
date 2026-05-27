"""AccountsScreen — sidebar entry #2.

Lists store-mapped accounts with state glyph + alias + linked YNAB record.

Actions:
  a — rename alias (AliasInputModal)
  l — relink (Plan 3: bell — full picker deferred to Plan 4)
  i — toggle ignore_transactions
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


_TRACKING_TYPES = {
    "otherAsset", "otherLiability", "mortgage", "autoLoan",
    "studentLoan", "personalLoan", "medicalDebt", "otherDebt",
}


def _state_glyph(account: dict) -> str:
    if account.get("ignore_transactions"):
        return "⏸"
    if (account.get("ynab") or {}).get("id"):
        return "✓"
    return "!"


class AccountsScreen(Container):
    """Sidebar entry #2 — browse and edit account mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None

    def compose(self) -> ComposeResult:
        yield ListView(id="accounts-list")

    def bind_data(self, *, store) -> None:
        self._store = store
        self.refresh_rows()

    def refresh_rows(self) -> None:
        lv = self.query_one("#accounts-list", ListView)
        lv.clear()
        if self._store is None:
            return
        for acc in self._store.accounts():
            glyph = _state_glyph(acc)
            alias = acc.get("alias", "?")
            ynab = acc.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            yn_type = ynab.get("type") or ""
            tag = " (tracking)" if yn_type in _TRACKING_TYPES else ""
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<22.22}  {yn_type}{tag}"
            lv.append(ListItem(Label(text)))

    def row_count(self) -> int:
        return len(list(self._store.accounts())) if self._store else 0

    def set_cursor(self, index: int) -> None:
        self.query_one("#accounts-list", ListView).index = index

    def _current_account(self) -> Optional[dict]:
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or self._store is None:
            return None
        accounts = list(self._store.accounts())
        if 0 <= idx < len(accounts):
            return accounts[idx]
        return None

    def action_toggle_ignore(self) -> None:
        acc = self._current_account()
        if acc is None or self._store is None:
            return
        self._store.set_account_ignore(acc["id"], not acc.get("ignore_transactions"))
        self.refresh_rows()

    def action_rename(self) -> None:
        acc = self._current_account()
        if acc is None or self._store is None:
            return
        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Rename '{acc['alias']}':",
            default=acc.get("alias", ""),
        )

        def _on_done(new_alias):
            if new_alias is None or new_alias == acc.get("alias"):
                return
            self._store.set_account_alias(acc["id"], new_alias)
            self.refresh_rows()

        self.app.push_screen(modal, callback=_on_done)

    def action_relink(self) -> None:
        # Plan 3 scope: bell. Plan 4 adds a proper picker over fetched
        # YNAB accounts (not the store's accounts).
        self.app.bell()
