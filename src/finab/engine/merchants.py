"""Pure helpers for phase 2 (merchant sync).

Extracted from finab.main to give the (future) TUI Merchants screen
a non-interactive surface. main.py re-exports them so existing call
sites keep working.

No interactive I/O (no input(), no ANSI colour). Diagnostic print()
calls inside _reconcile_store_merchants_to_ynab are retained verbatim
from the original — they're flagged with TODO(plan-2) markers so the
TUI work can replace them with a structured result later.
"""
from typing import TYPE_CHECKING, Optional

from finab.store import to_dict, normalize_alias

if TYPE_CHECKING:
    from finab.store import ConfigStore
    from finab.ynab_client import YNABClient


def _link_account_transfer_payee(
    store: "ConfigStore",
    ynab_payees,
    alias: str,
    fw_record: dict,
) -> bool:
    """If `alias` matches a stored account, link the merchant to that
    account's transfer payee (the "Transfer: <name>" payee YNAB auto-creates
    per account). Returns True on success — caller should not fall through
    to regular payee lookup."""
    account_match = store.account_by_alias(alias)
    if not account_match:
        return False
    transfer_payee_id = account_match.get("ynab", {}).get("transfer_payee_id")
    if not transfer_payee_id:
        return False
    transfer_payee = next(
        (p for p in ynab_payees if str(getattr(p, "id", "")) == str(transfer_payee_id)),
        None,
    )
    if not transfer_payee:
        return False
    store.add_merchant(
        alias=alias,
        fw_record=fw_record,
        ynab_record=to_dict(transfer_payee),
    )
    return True


def _record_merchant_alias(
    store: "ConfigStore",
    ynab_client: "YNABClient",
    budget_id: str,
    fw_merchant_id: str,
    alias: str,
    fw_merchant_name: Optional[str] = None,
) -> None:
    """Defensive fallback used by the transaction pipeline when a merchant id
    appears that wasn't covered by Phase 2. Same 4-way fork as sync_merchants."""
    fw_record = {"id": fw_merchant_id, "name": fw_merchant_name or fw_merchant_id}

    # 1. Existing merchant by alias?
    existing = store.merchant_by_alias(alias)
    if existing:
        store.attach_finwise_to_merchant(existing["id"], fw_record)
        return

    ynab_payees = ynab_client.get_payees(budget_id)

    # 2. Account-as-transfer: alias matches one of the user's own accounts.
    if _link_account_transfer_payee(store, ynab_payees, alias, fw_record):
        return

    # 3. Existing YNAB payee by name?
    ynab_match = next(
        (p for p in ynab_payees if normalize_alias(p.name) == normalize_alias(alias)),
        None,
    )
    if ynab_match:
        store.add_merchant(
            alias=alias,
            fw_record=fw_record,
            ynab_record=to_dict(ynab_match),
        )
        return

    # 4. Create new YNAB payee
    new_payee = ynab_client.create_payee(budget_id, alias)
    store.add_merchant(
        alias=alias,
        fw_record=fw_record,
        ynab_record=to_dict(new_payee),
    )


def _extract_distinct_merchants(fw_transactions) -> list[dict]:
    """Walk FinWise transactions and emit one record per unique merchant_id.

    FinWise has no merchant endpoint; merchant data lives on transactions.
    Captures a few sample transactions per merchant so the user has enough
    context to recognize the merchant when prompted (merchant_name is often
    null on the FinWise side).

    Operates on the unified `Transaction` model: amount is an int in
    milliunits (1000 = 1.00), date is a `date`, and the human-readable
    description lives in `memo` (with `original_description` as fallback).
    """
    seen: dict[str, dict] = {}
    samples: dict[str, list[dict]] = {}
    for t in fw_transactions:
        mid = getattr(t, "merchant_id", None)
        if not mid:
            continue

        memo = getattr(t, "memo", None)
        orig = getattr(t, "original_description", None)
        description = memo or orig or getattr(t, "payee_name", None)

        sample = {
            "description": description,
            "original_description": orig if orig and orig != memo else None,
            "amount": None,
            "date": None,
        }
        amt = getattr(t, "amount", None)
        if amt is not None:
            try:
                # Transaction.amount is int milliunits — convert to whole currency
                sample["amount"] = int(amt) / 1000.0
            except (TypeError, ValueError):
                pass
        d = getattr(t, "date", None)
        if d is not None:
            try:
                sample["date"] = d.isoformat() if hasattr(d, "isoformat") else str(d)
            except (AttributeError, TypeError):
                sample["date"] = str(d)

        if mid not in seen:
            seen[mid] = {
                "id": mid,
                "name": getattr(t, "merchant_name", None),
                "samples": [],
            }
            samples[mid] = []

        if len(samples[mid]) < 3:
            samples[mid].append(sample)
            seen[mid]["samples"] = samples[mid]

    return list(seen.values())


def _reconcile_store_merchants_to_ynab(
    store: "ConfigStore",
    ynab_payees,
    ynab_client: "YNABClient",
    budget_id: str,
) -> int:
    """For each merchant entry in the store, ensure its YNAB payee exists in
    YNAB. Create and update the store entry if missing. Returns count."""
    existing_ids = {str(p.id) for p in ynab_payees if getattr(p, "id", None) is not None}
    created = 0
    for entry in list(store.merchants()):
        yn = entry.get("ynab", {})
        yn_id = yn.get("id")
        if yn_id and str(yn_id) in existing_ids:
            continue

        name = entry.get("alias") or yn.get("name")
        if not name:
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Skipping merchant {entry.get('id')!r}: no alias/name to push")
            continue
        try:
            new_payee = ynab_client.create_payee(budget_id, name)
            store.set_merchant_ynab_record(entry["id"], to_dict(new_payee))
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Recreated YNAB payee '{name}'")
            created += 1
        except Exception as e:
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Failed to create YNAB payee '{name}': {e}")
    return created
