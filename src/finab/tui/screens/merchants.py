"""MerchantsScreen — sidebar entry #3.

Master/detail layout. Left pane: scrollable list of mapped + unmapped
merchants. Right pane: details for the highlighted merchant including
sample transactions for unmapped ones.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Label, ListItem, ListView

from finab.tui.widgets.merchant_card import MerchantCard


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
        self._fw_transactions: list = []
        self._ynab_payees: list = []
        self._ynab_client = None
        self._budget_id: Optional[str] = None
        # Row index → (kind, payload). kind in {"mapped", "unmapped"}.
        self._row_map: list = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(id="merchants-list")
            yield MerchantCard("(no merchant selected)", id="merchants-detail")

    def bind_data(
        self,
        *,
        store,
        fw_transactions: list = None,
        ynab_payees: list = None,
        ynab_client=None,
        budget_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._fw_transactions = list(fw_transactions) if fw_transactions is not None else []
        self._ynab_payees = list(ynab_payees) if ynab_payees is not None else []
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self.refresh_rows()

    def _unmapped_merchants(self) -> list:
        """Distinct FW merchants (from fw_transactions) not yet in the store."""
        if self._store is None:
            return []
        from finab.engine.merchants import _extract_distinct_merchants
        all_distinct = _extract_distinct_merchants(self._fw_transactions)
        # Collect all fw_ids across every mapped merchant's finwise dict.
        mapped_fw_ids = set()
        for m in self._store.merchants():
            mapped_fw_ids.update((m.get("finwise") or {}).keys())
        return [d for d in all_distinct if d["id"] not in mapped_fw_ids]

    def unmapped_count(self) -> int:
        """How many distinct FW merchants are still unmapped."""
        return len(self._unmapped_merchants())

    def refresh_rows(self) -> None:
        lv = self.query_one("#merchants-list", ListView)
        lv.clear()
        self._row_map = []
        if self._store is None:
            return

        # 1. Unmapped merchants — derive from fw_transactions.
        unmapped = self._unmapped_merchants()
        for fw_m in unmapped:
            name = fw_m.get("name") or "(no name)"
            text = f"!  {name:<22.22}  →  (unlinked — press `l` to map)"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("unmapped", fw_m))

        # 2. Mapped store merchants.
        for m in self._store.merchants():
            glyph = _merchant_glyph(m)
            alias = m.get("alias", "?")
            ynab = m.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            link_kind = "transfer payee" if ynab.get("transfer_account_id") else ("payee" if ynab.get("id") else "")
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<26.26}  {link_kind}"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("mapped", m))

        # After rebuilding the list, refresh the detail pane to match
        # whatever the cursor is on (or clear it if empty).
        self._refresh_detail()

    def row_count(self) -> int:
        return len(self._row_map)

    def has_unmapped_for(self, fw_id: str) -> bool:
        for kind, payload in self._row_map:
            if kind == "unmapped" and payload.get("id") == fw_id:
                return True
        return False

    def set_cursor(self, index: int) -> None:
        lv = self.query_one("#merchants-list", ListView)
        lv.index = index
        self._refresh_detail()

    def _current_row(self):
        lv = self.query_one("#merchants-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        return self._row_map[idx]

    def _current_merchant(self) -> Optional[dict]:
        row = self._current_row()
        if row is None or row[0] != "mapped":
            return None
        return row[1]

    def _current_unmapped(self):
        row = self._current_row()
        if row is None or row[0] != "unmapped":
            return None
        return row[1]

    def _refresh_detail(self) -> None:
        card = self.query_one("#merchants-detail", MerchantCard)
        row = self._current_row()
        if row is None:
            card.set_row(None, None)
            return
        kind, payload = row
        card.set_row(kind, payload)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """When the cursor moves in the merchants list, refresh the detail card.
        Sidebar highlights also fire ListView.Highlighted; filter by source."""
        lv = self.query_one("#merchants-list", ListView)
        if event.list_view is not lv:
            return
        self._refresh_detail()

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

    def action_link(self) -> None:
        """Map an unmapped merchant. Bells on a mapped row."""
        fw_m = self._current_unmapped()
        if fw_m is None:
            self.app.bell()
            return
        if self._ynab_client is None or self._budget_id is None:
            self.app.bell()
            return

        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Alias for merchant '{fw_m.get('name') or fw_m['id']}':",
            default=fw_m.get("name") or "",
        )

        def _on_alias(alias):
            if alias is None:
                return
            self._continue_link_flow(fw_m, alias)

        self.app.push_screen(modal, callback=_on_alias)

    def _continue_link_flow(self, fw_m: dict, alias: str) -> None:
        """Three-source resolution: store-account-as-transfer-payee,
        existing YNAB payee, or create new payee."""
        from finab.engine.merchants import _link_account_transfer_payee
        # 1. Does the alias match a store account? Link to that account's
        # transfer payee (own-account transfers).
        if _link_account_transfer_payee(self._store, self._ynab_payees, alias, fw_m):
            self.refresh_rows()
            return

        # 2. Existing YNAB payee by name?
        from finab.store import normalize_alias, to_dict
        match = next(
            (
                p for p in self._ynab_payees
                if normalize_alias(getattr(p, "name", "")) == normalize_alias(alias)
                and not getattr(p, "deleted", False)
                and not getattr(p, "transfer_account_id", None)
            ),
            None,
        )
        if match is not None:
            self._store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(match),
            )
            self.refresh_rows()
            return

        # 3. No match — confirm create. Enter confirms (not just `y`) so a
        # default-named merchant links in one keystroke from the prompt.
        from finab.tui.widgets.yes_no_modal import YesNoModal
        modal = YesNoModal(
            message=f"No YNAB payee named '{alias}' exists. Create a new one?",
            enter_confirms=True,
        )

        def _on_confirm(answer):
            if not answer:
                return
            self._create_and_link(fw_m, alias)

        self.app.push_screen(modal, callback=_on_confirm)

    def _create_and_link(self, fw_m: dict, alias: str) -> None:
        try:
            new_payee = self._ynab_client.create_payee(self._budget_id, alias)
        except Exception:
            self.app.bell()
            return
        from finab.store import to_dict
        self._store.add_merchant(
            alias=alias,
            fw_record=fw_m,
            ynab_record=to_dict(new_payee),
        )
        self._ynab_payees.append(new_payee)
        self.refresh_rows()
