"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
from datetime import date
from typing import Any, Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore, normalize_alias


# Names YNAB might use for the inflow category. Checked in this order.
_INFLOW_CATEGORY_NAMES = (
    "inflow: ready to assign",
    "ready to assign",
    "inflow: to be budgeted",
    "to be budgeted",
)


def _is_inflow(txn) -> bool:
    """A positive amount on a YNAB transaction is an inflow."""
    return getattr(txn, "amount", 0) > 0


def _is_transfer(merchant: Optional[dict]) -> bool:
    """A merchant whose YNAB record carries a transfer_account_id is a
    transfer payee — the transaction is a transfer to/from one of the
    user's own accounts."""
    if not merchant:
        return False
    return merchant.get("ynab", {}).get("transfer_account_id") is not None


def _find_inflow_category(categories) -> Optional[str]:
    """Find the YNAB category id for 'Inflow: Ready to Assign' (or its
    legacy variants). Returns the id of the first matching, non-hidden,
    non-deleted category; or None if none exists."""
    by_name = {}
    for c in categories:
        if getattr(c, "hidden", False) or getattr(c, "deleted", False):
            continue
        name = getattr(c, "name", "") or ""
        by_name[name.lower()] = c
    for candidate in _INFLOW_CATEGORY_NAMES:
        c = by_name.get(candidate)
        if c is not None:
            return c.id
    return None


class _PendingQueue:
    """Holds categorized-but-not-yet-pushed transactions. Flushed on demand
    via the `f` command, at end of run, or after Ctrl+C confirmation."""

    def __init__(self):
        self.creates: list = []
        self.updates: list = []

    def count(self) -> int:
        return len(self.creates) + len(self.updates)

    def add(self, txn) -> None:
        if getattr(txn, "ynab_id", None):
            self.updates.append(txn)
        else:
            self.creates.append(txn)

    def flush(self, ynab_client: YNABClient, budget_id: str) -> bool:
        """Push all pending transactions in two batched calls. Returns True
        if both succeed (queue clears). On any exception, returns False and
        keeps the queue for retry."""
        try:
            if self.creates:
                ynab_client.create_transactions(budget_id, self.creates)
            if self.updates:
                ynab_client.update_transactions(budget_id, self.updates)
            self.creates.clear()
            self.updates.clear()
            return True
        except Exception as e:
            print(f"Flush failed: {e}")
            return False


def sync_transactions(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
) -> None:
    """Phase 3 entry point. Stub for now; populated by later tasks."""
    raise NotImplementedError("sync_transactions wired in later tasks")
