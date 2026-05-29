"""TransactionCard — detail view of a single Candidate.

Editorial-terminal layout:
  AMOUNT       (large, sign-colored, the hero)
  Merchant     (cream, subtitle)
  Status badge (small, colored)
  Memo · Date  (muted, the supporting detail)

Updates via `set_candidate(c, alias_of)`. None clears to empty state.
"""
from typing import Callable, Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static

from finab.engine.sync import Candidate


def _amount_str(amount_milliunits: int) -> str:
    """Signed formatted amount with no currency symbol."""
    return f"{amount_milliunits / 1000:,.2f}"


_STATUS_LABELS = {
    "pending": "PENDING",
    "decided": "DECIDED",
    "auto": "AUTO",
    "flushed": "FLUSHED",
}


class TransactionCard(Container):
    """Editorial detail view of a Candidate."""

    DEFAULT_CSS = """
    TransactionCard {
        layout: vertical;
        height: auto;
    }

    TransactionCard.empty #card-amount,
    TransactionCard.empty #card-merchant,
    TransactionCard.empty #card-status,
    TransactionCard.empty #card-meta,
    TransactionCard.empty #card-warnings {
        display: none;
    }

    TransactionCard #card-empty {
        display: none;
    }

    TransactionCard.empty #card-empty {
        display: block;
        color: $text-muted;
        padding: 2 0;
    }

    #card-amount {
        text-style: bold;
        padding-bottom: 1;
    }

    #card-amount.negative {
        color: $accent;
    }

    #card-amount.positive {
        color: $success;
    }

    #card-merchant {
        color: $foreground;
        text-style: bold;
        padding-bottom: 1;
    }

    #card-status {
        text-style: bold;
        padding-bottom: 1;
    }

    #card-status.status-pending { color: $text-muted; }
    #card-status.status-decided { color: $success; }
    #card-status.status-auto { color: $secondary; }
    #card-status.status-flushed { color: $text-muted; }
    #card-status.status-warning { color: $warning; }

    #card-meta {
        color: $text-muted;
    }

    #card-warnings {
        color: $warning;
        padding-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("(select a transaction)", id="card-empty")
        yield Static("", id="card-amount")
        yield Static("", id="card-merchant")
        yield Static("", id="card-status")
        yield Static("", id="card-meta")
        yield Static("", id="card-warnings")

    def on_mount(self) -> None:
        self.add_class("empty")

    def set_candidate(
        self,
        candidate: Optional[Candidate],
        *,
        alias_of: Callable[[Candidate], str] = None,
    ) -> None:
        """Re-render to show this candidate. None clears to empty state."""
        if candidate is None:
            self.add_class("empty")
            return
        self.remove_class("empty")
        txn = candidate.txn
        alias = (alias_of(candidate) if alias_of else None) or "(no merchant)"
        amount_int = getattr(txn, "amount", 0)
        amount = _amount_str(amount_int)
        d = getattr(txn, "date", "?")
        memo = getattr(txn, "memo", "") or "(no memo)"

        # Amount section — the hero.
        amount_widget = self.query_one("#card-amount", Static)
        amount_widget.update(amount)
        amount_widget.remove_class("positive")
        amount_widget.remove_class("negative")
        amount_widget.add_class("positive" if amount_int > 0 else "negative")

        # Merchant.
        self.query_one("#card-merchant", Static).update(alias.upper())

        # Status badge.
        status_widget = self.query_one("#card-status", Static)
        label = _STATUS_LABELS.get(candidate.status, candidate.status.upper())
        if candidate.auto_reason:
            label = f"{label} · {candidate.auto_reason.upper()}"
        status_widget.update(label)
        for cls in ("status-pending", "status-decided", "status-auto", "status-flushed", "status-warning"):
            status_widget.remove_class(cls)
        if candidate.warnings:
            status_widget.add_class("status-warning")
        else:
            status_widget.add_class(f"status-{candidate.status}")

        # Meta (date + memo).
        self.query_one("#card-meta", Static).update(f"{d}   ·   {memo}")

        # Warnings.
        warnings_widget = self.query_one("#card-warnings", Static)
        if candidate.warnings:
            warnings_widget.update("\n".join(f"⚠ {w}" for w in candidate.warnings))
        else:
            warnings_widget.update("")
