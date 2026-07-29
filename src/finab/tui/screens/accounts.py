"""AccountsScreen — sidebar entry #2.

Lists store-mapped accounts with state glyph + alias + linked YNAB record.
Unmapped FW accounts (present in the live fetch but not yet in the store)
appear at the top with a `!` glyph.

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
        self._fw_accounts: list = []
        self._ynab_accounts: list = []
        self._ynab_client = None
        self._budget_id: Optional[str] = None
        # Row index → (kind, payload). kind in {"mapped", "unmapped"}.
        # payload: for "mapped", the store account dict; for "unmapped",
        # the FW account object.
        self._row_map: list = []

    def compose(self) -> ComposeResult:
        yield ListView(id="accounts-list")

    def bind_data(
        self,
        *,
        store,
        fw_accounts: list = None,
        ynab_accounts: list = None,
        ynab_client=None,
        budget_id: Optional[str] = None,
    ) -> None:
        self._store = store
        self._fw_accounts = list(fw_accounts) if fw_accounts is not None else []
        self._ynab_accounts = list(ynab_accounts) if ynab_accounts is not None else []
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self.refresh_rows()

    def _unmapped_fw_accounts(self) -> list:
        """FW accounts present in the live fetch but not yet in the store."""
        if self._store is None:
            return []
        mapped_fw_ids = {
            (a.get("finwise") or {}).get("id")
            for a in self._store.accounts()
        }
        return [
            fw for fw in self._fw_accounts
            if getattr(fw, "finwise_id", None) and fw.finwise_id not in mapped_fw_ids
        ]

    def unmapped_count(self) -> int:
        """How many FW accounts are still unmapped. Drives the setup
        wizard's strict accounts gate."""
        return len(self._unmapped_fw_accounts())

    def refresh_rows(self) -> None:
        lv = self.query_one("#accounts-list", ListView)
        lv.clear()
        self._row_map = []
        if self._store is None:
            return

        # 1. Unmapped FW accounts — any fw_account whose finwise_id isn't
        # in the store yet.
        unmapped = self._unmapped_fw_accounts()
        for fw in unmapped:
            text = f"!  {fw.name:<22.22}  →  (unlinked — press `l` to map)"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("unmapped", fw))

        # 2. Mapped store accounts.
        for acc in self._store.accounts():
            glyph = _state_glyph(acc)
            alias = acc.get("alias", "?")
            ynab = acc.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            yn_type = ynab.get("type") or ""
            tag = " (tracking)" if yn_type in _TRACKING_TYPES else ""
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<22.22}  {yn_type}{tag}"
            lv.append(ListItem(Label(text)))
            self._row_map.append(("mapped", acc))

    def row_count(self) -> int:
        return len(self._row_map)

    def has_unmapped_for(self, finwise_id: str) -> bool:
        """Test helper: did the unmapped row for this FW id render?"""
        for kind, payload in self._row_map:
            if kind == "unmapped" and getattr(payload, "finwise_id", None) == finwise_id:
                return True
        return False

    def set_cursor(self, index: int) -> None:
        self.query_one("#accounts-list", ListView).index = index

    def _current_account(self) -> Optional[dict]:
        """The store account at the cursor, or None if the row is unmapped
        or there's no cursor. Used by action_rename / action_toggle_ignore
        which only operate on mapped rows."""
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        kind, payload = self._row_map[idx]
        if kind == "mapped":
            return payload
        return None

    def _current_unmapped_fw(self):
        """Return the FW account stub at the current cursor, or None if
        the row is mapped (or there's no cursor). Used by Task 5's
        action_link."""
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        kind, payload = self._row_map[idx]
        if kind == "unmapped":
            return payload
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

    def action_link(self) -> None:
        """Map an unmapped FW account: prompt for alias, then either
        link to a YNAB account by name match or create a new one.

        On a mapped row, this is a no-op + bell (relink to a different
        YNAB account is a separate workflow not covered in Plan 4)."""
        fw = self._current_unmapped_fw()
        if fw is None:
            self.app.bell()
            return
        if self._ynab_client is None or self._budget_id is None:
            # Test paths without clients hit this; nothing to do.
            self.app.bell()
            return

        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Alias for '{fw.name}':",
            default=fw.name,
        )

        def _on_alias(alias):
            if alias is None:
                return
            self._continue_link_flow(fw, alias)

        self.app.push_screen(modal, callback=_on_alias)

    def _continue_link_flow(self, fw, alias: str) -> None:
        """After the alias is chosen, try to match against existing YNAB
        accounts; if no match, confirm-and-create."""
        from finab.store import normalize_alias
        match = next(
            (
                a for a in self._ynab_accounts
                if normalize_alias(getattr(a, "name", "")) == normalize_alias(alias)
                and not getattr(a, "deleted", False)
                and not getattr(a, "closed", False)
            ),
            None,
        )
        if match is not None:
            self._link_to_existing(fw, alias, match)
            return

        from finab.tui.widgets.yes_no_modal import YesNoModal
        modal = YesNoModal(
            message=f"No YNAB account named '{alias}' exists. Create a new one?",
        )

        def _on_confirm(answer):
            if not answer:
                return
            self._create_and_link(fw, alias)

        self.app.push_screen(modal, callback=_on_confirm)

    def _link_to_existing(self, fw, alias: str, ynab_acc) -> None:
        """Link an unmapped FW account to an existing YNAB account."""
        fw_record = {
            "id": fw.finwise_id,
            "name": fw.name,
            "type": getattr(fw, "type", "checking"),
            "balance": getattr(fw, "balance", 0),
            "currency_code": getattr(fw, "currency_code", "USD"),
        }
        ynab_record = {
            "id": str(getattr(ynab_acc, "id", "") or getattr(ynab_acc, "ynab_id", "")),
            "name": ynab_acc.name,
            "type": getattr(ynab_acc, "type", "checking"),
            "balance": getattr(ynab_acc, "balance", 0),
            "transfer_payee_id": getattr(ynab_acc, "transfer_payee_id", None),
        }
        self._store.add_account(
            alias=alias,
            fw_record=fw_record,
            ynab_record=ynab_record,
            ignore_transactions=False,
        )
        self.refresh_rows()

    def _create_and_link(self, fw, alias: str) -> None:
        """Create a new YNAB account and link the FW account to it."""
        from finab.models import Account
        payload = Account(
            name=alias,
            type=getattr(fw, "type", None) or "checking",
            balance=getattr(fw, "balance", 0) or 0,
            currency_code=getattr(fw, "currency_code", "") or "",
        )
        try:
            response = self._ynab_client.create_account(self._budget_id, payload)
        except Exception:
            self.app.bell()
            return
        new_record = response.data.account
        new_id = str(getattr(new_record, "id", "") or getattr(new_record, "ynab_id", ""))
        ynab_record = {
            "id": new_id,
            "name": getattr(new_record, "name", alias),
            "type": getattr(new_record, "type", payload.type),
            "balance": getattr(new_record, "balance", payload.balance),
            "transfer_payee_id": (
                str(new_record.transfer_payee_id)
                if getattr(new_record, "transfer_payee_id", None) is not None
                else None
            ),
        }
        fw_record = {
            "id": fw.finwise_id,
            "name": fw.name,
            "type": getattr(fw, "type", "checking"),
            "balance": getattr(fw, "balance", 0),
            "currency_code": getattr(fw, "currency_code", "USD"),
        }
        self._store.add_account(
            alias=alias,
            fw_record=fw_record,
            ynab_record=ynab_record,
            ignore_transactions=False,
        )
        self._ynab_accounts.append(new_record)
        self.refresh_rows()
