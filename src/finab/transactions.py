"""TransactionsStore — owns transactions.json, the durable map from
FinWise transaction UUIDs to our YNAB import_id.

The interactive prompt code that used to live in this module was
removed when the TUI became the default (Plan 3 cutover). The pure
helpers were already moved to finab.engine.sync (Plan 1) and are
re-exported below for backward compatibility with existing imports.
"""
import json
import os
from pathlib import Path
from typing import Optional


TRANSACTIONS_FILE = Path("transactions.json")


# --- Re-exports from finab.engine.sync ---
from finab.engine.sync import (
    _INFLOW_CATEGORY_NAMES,
    _TRACKING_ACCOUNT_TYPES,
    _account_is_tracking,
    _is_inflow,
    _is_before_current_month,
    _is_transfer,
    _find_inflow_category,
    _closest_processing,
    _apply_repeat,
    _apply_processing_to_txn,
    _update_merchant_memory,
    _category_name,
    _render_splits,
    _sort_key,
    merge_and_filter_transactions,
    match_transfer_pairs,
    TransferMatch,
)


class TransactionsStore:
    """Owns transactions.json: a map from FinWise transaction UUIDs to our
    durable YNAB import_id (a random uuid4 hex). The import_id is sent to
    YNAB on each push and is what we dedup against on subsequent fetches.

    When a previously-synced YNAB transaction is missing from the live
    YNAB fetch (user deleted it), the import_id is rotated: a new uuid is
    generated, replacing the stored one, and the FW transaction is pushed
    as new. This sidesteps YNAB's phantom-import_id behaviour (which would
    otherwise silently no-op a re-push using the deleted-but-remembered id).
    """

    def __init__(self, path: Optional[Path] = None):
        # Resolve default lazily so tests can monkey-patch TRANSACTIONS_FILE
        # via conftest. A def-time default captures the constant by value
        # and would defeat the sandbox.
        if path is None:
            path = TRANSACTIONS_FILE
        self.path = Path(path)
        self._data = self._load()
        self._data.setdefault("synced_transactions", {})

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=4, default=str)
        os.replace(tmp, self.path)

    def import_id_for(self, fw_uuid: str) -> Optional[str]:
        """Return the durable YNAB import_id we previously assigned to
        this FinWise transaction, or None if it hasn't been synced."""
        return self._data["synced_transactions"].get(fw_uuid)

    def record(self, fw_uuid: str, import_id: str) -> None:
        """Persist a single fw -> import_id mapping."""
        self._data["synced_transactions"][fw_uuid] = import_id
        self._save()

    def remove(self, fw_uuid: str) -> None:
        """Drop a stale mapping."""
        if fw_uuid in self._data["synced_transactions"]:
            del self._data["synced_transactions"][fw_uuid]
            self._save()

    def prune_stale(self, live_import_ids: set) -> int:
        """Drop any mapping whose import_id is not in the live YNAB fetch.
        Returns the number of entries removed."""
        kept = {
            fw: iid
            for fw, iid in self._data["synced_transactions"].items()
            if iid in live_import_ids
        }
        removed = len(self._data["synced_transactions"]) - len(kept)
        if removed:
            self._data["synced_transactions"] = kept
            self._save()
        return removed
