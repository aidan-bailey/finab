"""Reconstruct transactions.json from live FinWise + YNAB data.

Each YNAB transaction that was previously pushed by finab carries our
generated 32-char-hex import_id. By matching YNAB transactions to
FinWise transactions on (account, date, amount), we can rebuild the
fw_uuid -> import_id mapping that lives in transactions.json.

Match key: (ynab_account_id, date, amount). Both sides are converted
to the YNAB account_id via the store. Ambiguous matches (multiple FW
transactions on the same key) are skipped with a warning — the user
can re-categorize those manually if needed.

Run with: uv run python scripts/recover_transactions_store.py
"""

from __future__ import annotations
import json
from collections import defaultdict
from pathlib import Path

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.config import load_budget_id
from finab.store import ConfigStore
from finab.transactions import TransactionsStore


def main() -> int:
    budget_id = load_budget_id()
    if not budget_id:
        print("No budget_id in config.json")
        return 1

    fw = FinWiseClient()
    yc = YNABClient()
    store = ConfigStore()
    tx_store = TransactionsStore()

    existing = dict(tx_store._data.get("synced_transactions", {}))
    print(f"Existing transactions.json mappings: {len(existing)}")

    print("Fetching FinWise transactions...")
    fw_txns = fw.get_transactions()
    print(f"  {len(fw_txns)} FinWise transactions")

    print("Fetching YNAB transactions...")
    yn_txns = yc.get_transactions(budget_id)
    print(f"  {len(yn_txns)} YNAB transactions")

    # Build (ynab_account_id, date, amount) -> fw_txn lookup.
    # Drop FW transactions on accounts that aren't in the store or are
    # ignored (their YNAB twins are the duplicate-leg aggregator entries
    # we never want to bind).
    fw_by_key: dict[tuple, list] = defaultdict(list)
    for t in fw_txns:
        acc = store.account_by_finwise_id(t.account_id)
        if not acc:
            continue
        if acc.get("ignore_transactions"):
            continue
        yn_acc_id = acc.get("ynab", {}).get("id")
        if not yn_acc_id:
            continue
        key = (str(yn_acc_id), t.date, t.amount)
        fw_by_key[key].append(t)

    print(f"  Indexed {sum(len(v) for v in fw_by_key.values())} FW txns "
          f"under {len(fw_by_key)} unique (account, date, amount) keys")

    recovered: dict[str, str] = {}
    no_match = 0
    ambiguous = 0
    no_import_id = 0
    already_known = 0

    for y in yn_txns:
        if getattr(y, "deleted", False):
            continue
        iid = getattr(y, "import_id", None)
        if not iid:
            no_import_id += 1
            continue
        iid = str(iid)
        # Only attempt to recover finab-shaped import_ids: 32-char lowercase
        # hex. Skip YNAB-native ones like 'YNAB:-29423:2015-12-30:1'.
        if len(iid) != 32 or any(c not in "0123456789abcdef" for c in iid):
            continue

        key = (str(y.account_id), y.var_date, y.amount)
        candidates = fw_by_key.get(key, [])
        if not candidates:
            no_match += 1
            continue
        if len(candidates) > 1:
            ambiguous += 1
            continue

        fw = candidates[0]
        fw_uuid = fw.import_id
        if not fw_uuid:
            continue
        if fw_uuid in recovered:
            ambiguous += 1
            continue
        # If we already knew this mapping AND it points at the same iid,
        # it's a no-op; otherwise our recovery supersedes.
        if existing.get(fw_uuid) == iid:
            already_known += 1
        recovered[fw_uuid] = iid

    print()
    print("Summary:")
    print(f"  recovered mappings:        {len(recovered)}")
    print(f"    of which already-known:  {already_known}")
    print(f"  YNAB w/o finab import_id:  {no_import_id}")
    print(f"  YNAB with no FW match:     {no_match}")
    print(f"  ambiguous (multi-FW):      {ambiguous}")

    # Merge: keep any existing entries that the recovery didn't overwrite
    # (they may be real fw_uuids whose YNAB twins we couldn't match by
    # date/amount — e.g. cleared-vs-pending date drift).
    merged = {**existing, **recovered}

    out_path = Path("transactions.json")
    backup = out_path.with_suffix(".json.bak")
    if out_path.exists():
        out_path.replace(backup)
        print(f"Backed up existing -> {backup}")

    out_path.write_text(json.dumps({"synced_transactions": merged}, indent=4))
    print(f"Wrote {len(merged)} mappings to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
