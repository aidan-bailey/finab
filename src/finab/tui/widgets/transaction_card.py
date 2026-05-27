"""TransactionCard — detail view of a single Candidate.

Updates via `set_candidate(c, alias_of)` from the parent screen. When
called with None, shows the empty state.
"""
from typing import Callable, Optional

from textual.widgets import Static

from finab.engine.sync import Candidate


_STATUS_LABELS = {
    "pending": "pending — needs decision",
    "decided": "decided",
    "auto": "auto-resolved",
    "flushed": "pushed to YNAB",
}


def _amount_str(amount_milliunits: int) -> str:
    return f"{amount_milliunits / 1000:.2f}"


class TransactionCard(Static):
    """A read-only render of a Candidate's details."""

    def set_candidate(
        self,
        candidate: Optional[Candidate],
        *,
        alias_of: Callable[[Candidate], str] = None,
    ) -> None:
        """Re-render to show this candidate. None clears to empty state."""
        if candidate is None:
            self.update("(select a transaction)")
            return
        txn = candidate.txn
        alias = (alias_of(candidate) if alias_of else None) or "(no merchant)"
        amount = _amount_str(getattr(txn, "amount", 0))
        d = getattr(txn, "date", "?")
        memo = getattr(txn, "memo", "") or "(no memo)"
        status_extra = ""
        if candidate.auto_reason:
            status_extra = f" ({candidate.auto_reason})"
        status_label = _STATUS_LABELS.get(candidate.status, candidate.status) + status_extra
        lines = [
            f"Merchant:  {alias}",
            f"Date:      {d}",
            f"Amount:    {amount}",
            f"Memo:      {memo}",
            f"Status:    {status_label}",
        ]
        if candidate.warnings:
            lines.append("")
            for w in candidate.warnings:
                lines.append(f"⚠ {w}")
        self.update("\n".join(lines))
