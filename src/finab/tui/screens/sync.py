"""Sync screen — phase 3 transaction processing.

Layout: master/detail. The screen owns the candidates list and the
alias-lookup callable; widgets are dumb views of that state.
"""
from typing import Callable, Iterable, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import ListView

from finab.engine.sync import Candidate, SyncEngine
from finab.tui.widgets.category_picker import CategoryPickerModal
from finab.tui.widgets.history_picker import HistoryPickerModal
from finab.tui.widgets.pending_list import PendingList
from finab.tui.widgets.split_editor import SplitEditorModal
from finab.tui.widgets.transaction_card import TransactionCard


def _placeholder_alias_of(candidate) -> str:
    return getattr(candidate.txn, "_alias", "?")


class SyncScreen(Container):
    """The Sync screen container — embedded in FinabApp's ContentSwitcher."""

    def __init__(self, *, id: str = None):
        super().__init__(id=id)
        self._candidates: list[Candidate] = []
        self._alias_of: Callable[[Candidate], str] = _placeholder_alias_of
        self._account_of: Optional[Callable[[Candidate], str]] = None
        self._engine = None
        self._store = None
        self._tx_store = None
        self._ynab_payees: list = []

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PendingList(
                candidates=[],
                alias_of=_placeholder_alias_of,
                id="sync-pending",
            )
            yield TransactionCard(id="sync-detail")

    def set_candidates(
        self,
        candidates: Iterable[Candidate],
        *,
        alias_of: Callable[[Candidate], str],
        account_of: Optional[Callable[[Candidate], str]] = None,
    ) -> None:
        """Replace the screen's candidate list. Updates PendingList in-place
        and refreshes the card."""
        self._candidates = list(candidates)
        self._alias_of = alias_of
        self._account_of = account_of

        # Update PendingList in-place using clear-and-rebuild (same pattern as
        # PendingList.refresh_row — avoids mount(before=) reliability issues).
        old_pl = self.query_one("#sync-pending", PendingList)
        old_pl._candidates = list(self._candidates)
        old_pl._alias_of = alias_of
        old_pl.clear()
        for c in old_pl._candidates:
            old_pl.append(old_pl._row(c))

        # Refresh detail with the first candidate (if any).
        card = self.query_one("#sync-detail", TransactionCard)
        if self._candidates:
            card.set_candidate(self._candidates[0], alias_of=alias_of, account_of=account_of)
        else:
            card.set_candidate(None)

    def bind_data(self, *, loaded, store, tx_store, ynab_payees=None) -> None:
        """Build a SyncEngine from loaded data and push its candidates
        into the view. The screen retains references to the engine and
        store so subsequent actions (apply, undo, flush) can dispatch."""
        self._store = store
        self._tx_store = tx_store
        self._ynab_payees = list(ynab_payees) if ynab_payees is not None else []
        from finab.config import load_transfer_match_window_days
        self._engine = SyncEngine(
            fw_transactions=loaded.fw_transactions,
            ynab_transactions=loaded.ynab_transactions,
            ynab_categories=loaded.ynab_categories,
            store=store,
            tx_store=tx_store,
            transfer_match_window_days=load_transfer_match_window_days(),
        )

        def alias_of(candidate):
            merchant_id = getattr(candidate.txn, "merchant_id", None)
            if not merchant_id:
                return None
            merchant = store.merchant_by_finwise_id(merchant_id)
            return merchant.get("alias") if merchant else None

        def account_of(candidate):
            acc = store.account_by_ynab_id(getattr(candidate.txn, "account_id", None) or "")
            return acc["alias"] if acc else None

        self.set_candidates(self._engine.candidates, alias_of=alias_of, account_of=account_of)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """When the cursor in PendingList moves, refresh the detail card."""
        pl = self.query_one("#sync-pending", PendingList)
        if event.list_view is not pl:
            return  # not our list (e.g., the sidebar)
        current = pl.current_candidate()
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(current, alias_of=self._alias_of, account_of=self._account_of)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """ListView fires Selected on Enter. For the pending list, treat that
        as 'repeat closest history entry' — same behavior as the App's `enter`
        binding triggers, but routed through the message bus so it doesn't
        clash with modal Input.Submitted handlers."""
        pl = self.query_one("#sync-pending", PendingList)
        if event.list_view is not pl:
            return
        self.action_repeat_closest()

    # ---- action methods (called from FinabApp BINDINGS) ----

    def _current_candidate(self):
        pl = self.query_one("#sync-pending", PendingList)
        return pl.current_candidate()

    def action_category(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant = None
        if getattr(c.txn, "merchant_id", None):
            merchant = self._store.merchant_by_finwise_id(c.txn.merchant_id)
        used = (merchant or {}).get("categories_used") or {}
        alias = (merchant or {}).get("alias") or "?"
        modal = CategoryPickerModal(
            categories=self._engine._ynab_categories,
            used_categories=used,
            merchant_alias=alias,
        )

        def _on_picked(category_id):
            if category_id is None:
                return
            partner_id = getattr(c, "transfer_partner_id", None)
            self._engine.apply_category(c.id, category_id=category_id)
            self._refresh_after_decision(c.id)
            if partner_id:
                self.query_one("#sync-pending", PendingList).refresh_row(partner_id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_split(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant = None
        if getattr(c.txn, "merchant_id", None):
            merchant = self._store.merchant_by_finwise_id(c.txn.merchant_id)
        used = (merchant or {}).get("categories_used") or {}
        alias = (merchant or {}).get("alias") or "?"
        modal = SplitEditorModal(
            txn_amount=c.txn.amount,
            categories=self._engine._ynab_categories,
            used_categories=used,
            merchant_alias=alias,
        )

        def _on_done(splits):
            if splits is None:
                return
            partner_id = getattr(c, "transfer_partner_id", None)
            self._engine.apply_split(c.id, splits=splits)
            self._refresh_after_decision(c.id)
            if partner_id:
                self.query_one("#sync-pending", PendingList).refresh_row(partner_id)

        self.app.push_screen(modal, callback=_on_done)

    def action_history(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant_id = getattr(c.txn, "merchant_id", None)
        if not merchant_id:
            return
        merchant = self._store.merchant_by_finwise_id(merchant_id)
        if not merchant:
            return
        processings = merchant.get("processings") or {}
        if not processings:
            return
        modal = HistoryPickerModal(processings=processings, txn_amount=c.txn.amount)

        def _on_picked(result):
            if result is None:
                return
            _amount_key, entry = result
            partner_id = getattr(c, "transfer_partner_id", None)
            self._engine.apply_history(c.id, entry=entry)
            self._refresh_after_decision(c.id)
            if partner_id:
                self.query_one("#sync-pending", PendingList).refresh_row(partner_id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_undo(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        partner_id = getattr(c, "transfer_partner_id", None)
        try:
            self._engine.undo(c.id)
        except ValueError:
            # Not undoable (wrong status / no prior_state / flushed transfer) — bell.
            self.app.bell()
            return
        self._refresh_after_decision(c.id)
        if partner_id:
            pl = self.query_one("#sync-pending", PendingList)
            pl.refresh_row(partner_id)

    def action_flush(self) -> None:
        if self._engine is None:
            return
        ynab_client = getattr(self.app, "_ynab_client", None)
        budget_id = getattr(self.app, "_budget_id", None)
        if ynab_client is None or not budget_id:
            self.app.bell()
            return
        try:
            self._engine.flush(ynab_client, budget_id)
        except Exception:
            # Plan 2: bell on failure. Plan 3 should surface the error.
            self.app.bell()
            return
        # Refresh all rows — many candidates may have moved to 'flushed'.
        pl = self.query_one("#sync-pending", PendingList)
        for c in pl.candidates:
            pl.refresh_row(c.id)

    def action_repeat_closest(self) -> None:
        """Apply the closest-amount processing entry for the merchant
        of the current candidate. No-op if no merchant or no processings."""
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant_id = getattr(c.txn, "merchant_id", None)
        if not merchant_id:
            self.app.bell()
            return
        merchant = self._store.merchant_by_finwise_id(merchant_id)
        if not merchant:
            self.app.bell()
            return
        from finab.engine.sync import _closest_processing
        closest = _closest_processing(merchant, c.txn)
        if closest is None:
            self.app.bell()
            return
        _, entry = closest
        partner_id = getattr(c, "transfer_partner_id", None)
        self._engine.apply_history(c.id, entry=entry)
        self._refresh_after_decision(c.id)
        if partner_id:
            self.query_one("#sync-pending", PendingList).refresh_row(partner_id)

    def action_force_transfer(self) -> None:
        """On a suggested transfer, confirm the pre-computed pair. Otherwise
        open the manual account picker (one-sided / undetected transfers)."""
        c = self._current_candidate()
        if c is None or self._engine is None or self._store is None:
            return
        if c.transfer_role == "keep" and c.auto_reason == "transfer-suggested":
            self._engine.confirm_transfer_match(c.id)
            self._refresh_after_decision(c.id)
            return
        self._open_force_transfer_picker(c)

    def _open_force_transfer_picker(self, c: Candidate) -> None:
        from finab.tui.widgets.account_link_picker import AccountLinkPicker
        modal = AccountLinkPicker(store=self._store, title="Force transfer to which account?")

        def _on_picked(transfer_payee_id):
            if transfer_payee_id is None:
                return
            partner_id = getattr(c, "transfer_partner_id", None)
            self._engine.apply_transfer(c.id, transfer_payee_id=transfer_payee_id)
            self._refresh_after_decision(c.id)
            if partner_id:
                self.query_one("#sync-pending", PendingList).refresh_row(partner_id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_top(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = 0
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of, account_of=self._account_of)

    def action_bottom(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = len(pl.candidates) - 1
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of, account_of=self._account_of)

    def action_map_merchant(self) -> None:
        """Open the alias flow for the current no-merchant candidate.

        Runs the same 3-source resolution as MerchantsScreen.action_link:
        store-account-as-transfer-payee → existing YNAB payee → create new.
        After the merchant is linked, re-evaluates the candidate in-place
        (transfer / pre-month / plain pending) without rebuilding the engine.
        """
        c = self._current_candidate()
        if c is None or c.auto_reason != "no-merchant" or self._store is None:
            self.app.bell()
            return
        merchant_id = getattr(c.txn, "merchant_id", None)
        if not merchant_id:
            self.app.bell()
            return
        merchant_name = getattr(c.txn, "merchant_name", None)
        fw_record = {"id": merchant_id, "name": merchant_name or merchant_id}

        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Alias for '{merchant_name or merchant_id}':",
            default=merchant_name or "",
        )

        def _on_alias(alias):
            if alias is None:
                return
            self._link_merchant_flow(c, fw_record, alias)

        self.app.push_screen(modal, callback=_on_alias)

    def _link_merchant_flow(self, c: Candidate, fw_record: dict, alias: str) -> None:
        from finab.engine.merchants import _link_account_transfer_payee
        from finab.store import normalize_alias, to_dict

        # 1. Account-as-transfer: alias matches one of the user's own accounts.
        if _link_account_transfer_payee(self._store, self._ynab_payees, alias, fw_record):
            self._after_merchant_linked(c)
            return

        # 2. Existing YNAB payee by name.
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
            self._store.add_merchant(alias=alias, fw_record=fw_record, ynab_record=to_dict(match))
            self._after_merchant_linked(c)
            return

        # 3. No match — confirm create.
        from finab.tui.widgets.yes_no_modal import YesNoModal
        modal = YesNoModal(message=f"No YNAB payee named '{alias}' exists. Create a new one?")

        def _on_confirm(answer):
            if not answer:
                return
            ynab_client = getattr(self.app, "_ynab_client", None)
            budget_id = getattr(self.app, "_budget_id", None)
            if not ynab_client or not budget_id:
                self.app.bell()
                return
            try:
                new_payee = ynab_client.create_payee(budget_id, alias)
            except Exception:
                self.app.bell()
                return
            self._ynab_payees.append(new_payee)
            self._store.add_merchant(alias=alias, fw_record=fw_record, ynab_record=to_dict(new_payee))
            self._after_merchant_linked(c)

        self.app.push_screen(modal, callback=_on_confirm)

    def _after_merchant_linked(self, c: Candidate) -> None:
        """Re-evaluate a formerly no-merchant candidate after its merchant is linked."""
        from finab.engine.sync import _is_transfer, _is_before_current_month
        merchant_id = getattr(c.txn, "merchant_id", None)
        merchant = self._store.merchant_by_finwise_id(merchant_id) if merchant_id else None
        if merchant is None:
            return

        if _is_transfer(merchant):
            c.txn.payee_id = merchant["ynab"]["id"]
            c.txn.payee_name = None
            c.txn.category_id = None
            c.txn.subtransactions = []
            c.status = "auto"
            c.auto_reason = "transfer"
        elif _is_before_current_month(c.txn):
            c.txn.payee_id = merchant["ynab"].get("id")
            c.txn.payee_name = None
            c.txn.category_id = None
            c.txn.subtransactions = []
            c.status = "pending"
            c.auto_reason = "pre-month"
        else:
            c.txn.payee_id = merchant["ynab"].get("id")
            c.txn.payee_name = None
            c.status = "pending"
            c.auto_reason = None

        pl = self.query_one("#sync-pending", PendingList)
        pl.refresh_row(c.id)
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(c, alias_of=self._alias_of, account_of=self._account_of)
        try:
            self.app._refresh_header_stats()
        except Exception:
            pass

    def _refresh_after_decision(self, candidate_id: str) -> None:
        """After an engine.apply_*, rebuild the row and move cursor down one."""
        pl = self.query_one("#sync-pending", PendingList)
        pl.refresh_row(candidate_id)
        # Move cursor to next pending row if there is one.
        next_idx = (pl.index + 1) if pl.index is not None else 0
        if next_idx < len(pl.candidates):
            pl.index = next_idx
        # Refresh the detail card.
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(pl.current_candidate(), alias_of=self._alias_of, account_of=self._account_of)
