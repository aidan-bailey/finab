"""Pure helpers for phase 1 (account sync).

Extracted from finab.main to give the (future) TUI Accounts screen a
non-interactive surface. main.py re-exports them so existing call
sites keep working.

No interactive I/O (no input(), no ANSI colour). Diagnostic print()
calls inside _reconcile_store_accounts_to_ynab are retained verbatim
from the original — they're flagged with TODO(plan-2) markers so the
TUI work can replace them with a structured result later.
"""
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from finab.store import ConfigStore
    from finab.ynab_client import YNABClient


def _calculate_starting_balance(fw_acc, fw_client) -> int:
    """Reproduce today's balance-adjustment math: starting_balance =
    current_balance - sum(transactions since start of month)."""
    start_date = date.today().replace(day=1)
    try:
        txns = fw_client.get_transactions(start_date=start_date)
    except Exception:
        return fw_acc.balance
    account_txns = [t for t in txns if t.account_id == fw_acc.finwise_id]
    adjustment = sum(t.amount for t in account_txns)
    return int(fw_acc.balance - adjustment)


def _account_with_overrides(fw_acc, name: str, balance: int):
    """Return a shallow copy of fw_acc with name and balance overridden,
    suitable to pass to ynab_client.create_account."""
    import copy
    copy_acc = copy.copy(fw_acc)
    copy_acc.name = name
    copy_acc.balance = balance
    return copy_acc


def _reconcile_store_accounts_to_ynab(
    store: "ConfigStore",
    ynab_accounts,
    ynab_client: "YNABClient",
    budget_id: str,
) -> int:
    """For each account entry in the store, ensure its YNAB-side counterpart
    exists in YNAB. Create on YNAB and update the store entry if missing.
    Returns the number of accounts created on the YNAB side."""
    existing_ids = {
        str(getattr(a, "ynab_id", None) or getattr(a, "id", ""))
        for a in ynab_accounts
        if (getattr(a, "ynab_id", None) or getattr(a, "id", None))
    }
    created = 0
    for entry in list(store.accounts()):
        yn = entry.get("ynab", {})
        yn_id = yn.get("id")
        if yn_id and str(yn_id) in existing_ids:
            continue

        # Need to create on YNAB. Derive name/type/balance from what we have.
        name = entry.get("alias") or yn.get("name")
        if not name:
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Skipping account {entry.get('id')!r}: no alias/name to push")
            continue
        fw = entry.get("finwise", {}) or {}
        acc_type = yn.get("type") or fw.get("type") or "checking"
        balance = yn.get("balance")
        if balance is None:
            balance = fw.get("balance", 0)
        currency = fw.get("currency_code", "")

        try:
            from finab.models import Account
            payload = Account(
                name=name,
                type=acc_type,
                balance=int(balance) if balance is not None else 0,
                currency_code=currency,
            )
            response = ynab_client.create_account(budget_id, payload)
            new_record = response.data.account
            store.set_account_ynab_record(
                entry["id"],
                {
                    "id": str(getattr(new_record, "id", "")),
                    "name": getattr(new_record, "name", name),
                    "type": getattr(new_record, "type", acc_type),
                    "balance": getattr(new_record, "balance", balance),
                    "transfer_payee_id": (
                        str(new_record.transfer_payee_id)
                        if getattr(new_record, "transfer_payee_id", None) is not None
                        else None
                    ),
                },
            )
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Recreated YNAB account '{name}'")
            created += 1
        except Exception as e:
            # TODO(plan-2): replace with structured result for TUI
            print(f"  Failed to create YNAB account '{name}': {e}")
    return created
