"""Sync screen — phase 3 transaction processing.

Layout: master/detail. The screen owns the candidates list and the
alias-lookup callable; widgets are dumb views of that state.
"""
from typing import Callable, Iterable, Optional

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import ListView

from finab.engine.sync import Candidate, SyncEngine
from finab.tui.widgets.pending_list import PendingList
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
