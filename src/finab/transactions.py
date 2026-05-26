"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
from datetime import date
from typing import Any, Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore, normalize_alias


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
