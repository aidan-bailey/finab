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


def merge_and_filter_transactions(fw_transactions, ynab_transactions, store: ConfigStore) -> list:
    """Map FinWise accounts to YNAB account ids via the store, dedup against
    existing YNAB transactions by hashed import_id, and skip ones already
    categorized in YNAB. Returns the list of FinWise transactions needing
    processing. Each returned transaction has:
      - account_id rewritten to the YNAB account id
      - import_id rewritten to the hashed form
      - ynab_id set if a matching uncategorized YNAB transaction was found
        (so the caller knows to PATCH instead of POST)
    """
    from finab.main import generate_import_id  # local import to avoid cycle
    from finab.config import load_import_id_offset

    offset = load_import_id_offset()

    ynab_by_import_id = {}
    for txn in ynab_transactions:
        if getattr(txn, "import_id", None):
            ynab_by_import_id[txn.import_id] = txn

    out = []
    matched_ynab_ids = set()
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue

        hashed_id = generate_import_id(fw_txn.import_id, offset)
        fw_txn.import_id = hashed_id

        ynab_match = ynab_by_import_id.get(hashed_id)
        if ynab_match and ynab_match.id not in matched_ynab_ids:
            matched_ynab_ids.add(ynab_match.id)
            if getattr(ynab_match, "deleted", False):
                continue
            if ynab_match.category_id:
                # Already categorized — preserve user's manual YNAB work.
                continue
            fw_txn.ynab_id = ynab_match.id
            fw_txn.category_id = None

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out


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
