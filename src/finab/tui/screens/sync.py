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
        self._engine = None
        self._store = None
        self._tx_store = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PendingList(
                candidates=[],
                alias_of=_placeholder_alias_of,
                id="sync-pending",
            )
            yield TransactionCard("(select a transaction)", id="sync-detail")

    def set_candidates(
        self,
        candidates: Iterable[Candidate],
        *,
        alias_of: Callable[[Candidate], str],
    ) -> None:
        """Replace the screen's candidate list. Updates PendingList in-place
        and refreshes the card."""
        self._candidates = list(candidates)
        self._alias_of = alias_of

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
            card.set_candidate(self._candidates[0], alias_of=alias_of)
        else:
            card.set_candidate(None)

    def bind_data(self, *, loaded, store, tx_store) -> None:
        """Build a SyncEngine from loaded data and push its candidates
        into the view. The screen retains references to the engine and
        store so subsequent actions (apply, undo, flush) can dispatch."""
        self._store = store
        self._tx_store = tx_store
        self._engine = SyncEngine(
            fw_transactions=loaded.fw_transactions,
            ynab_transactions=loaded.ynab_transactions,
            ynab_categories=loaded.ynab_categories,
            store=store,
            tx_store=tx_store,
        )

        def alias_of(candidate):
            merchant_id = getattr(candidate.txn, "merchant_id", None)
            if not merchant_id:
                return None
            merchant = store.merchant_by_finwise_id(merchant_id)
            return merchant.get("alias") if merchant else None

        self.set_candidates(self._engine.candidates, alias_of=alias_of)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """When the cursor in PendingList moves, refresh the detail card."""
        pl = self.query_one("#sync-pending", PendingList)
        if event.list_view is not pl:
            return  # not our list (e.g., the sidebar)
        current = pl.current_candidate()
        card = self.query_one("#sync-detail", TransactionCard)
        card.set_candidate(current, alias_of=self._alias_of)

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
            self._engine.apply_category(c.id, category_id=category_id)
            self._refresh_after_decision(c.id)

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
            self._engine.apply_split(c.id, splits=splits)
            self._refresh_after_decision(c.id)

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
            self._engine.apply_history(c.id, entry=entry)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_undo(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        try:
            self._engine.undo(c.id)
        except ValueError:
            # Not a decided candidate — no-op + bell.
            self.app.bell()
            return
        self._refresh_after_decision(c.id)

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
        self._engine.apply_history(c.id, entry=entry)
        self._refresh_after_decision(c.id)

    def action_force_transfer(self) -> None:
        """Open AccountLinkPicker; selected account's transfer_payee_id
        is passed to engine.apply_transfer."""
        c = self._current_candidate()
        if c is None or self._engine is None or self._store is None:
            return
        from finab.tui.widgets.account_link_picker import AccountLinkPicker
        modal = AccountLinkPicker(store=self._store, title="Force transfer to which account?")

        def _on_picked(transfer_payee_id):
            if transfer_payee_id is None:
                return
            self._engine.apply_transfer(c.id, transfer_payee_id=transfer_payee_id)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_top(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = 0
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)

    def action_bottom(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = len(pl.candidates) - 1
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)

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
        card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)
