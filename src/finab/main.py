from datetime import date
from typing import Optional
import sys
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.config import (
    load_budget_id,
    save_budget_id,
)
from finab.store import ConfigStore, to_dict, normalize_alias
from finab.transactions import sync_transactions


def _color(code: str, s: str) -> str:
    """Wrap `s` in an ANSI color escape if stdout is a TTY; otherwise return plain."""
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"


def _bold(s: str) -> str:   return _color("1", s)
def _dim(s: str) -> str:    return _color("2", s)
def _red(s: str) -> str:    return _color("31", s)
def _green(s: str) -> str:  return _color("32", s)
def _yellow(s: str) -> str: return _color("33", s)
def _cyan(s: str) -> str:   return _color("36", s)


def _prompt_alias_required(prompt: str, default: Optional[str] = None) -> str:
    """Prompt the user for an alias. Re-prompts until a non-empty value is entered."""
    while True:
        if default:
            shown = f"{prompt} (default: '{default}'): "
        else:
            shown = f"{prompt}: "
        raw = input(shown).strip()
        if raw:
            return raw
        if default:
            return default
        print("Alias is required. Please enter a value.")


def _gather_pickable_entries(store: "ConfigStore") -> list[dict]:
    """Flat list of all alias-selectable entries: merchants first, then accounts."""
    entries = []
    for m in sorted(store.merchants(), key=lambda x: x["alias"].lower()):
        entries.append({"kind": "merchant", "alias": m["alias"]})
    for a in sorted(store.accounts(), key=lambda x: x["alias"].lower()):
        entries.append({"kind": "account", "alias": a["alias"]})
    return entries


def _interactive_pick(store: "ConfigStore") -> Optional[str]:
    """Show a numbered list of existing entries; return the alias the user picks,
    or None if they bail back to free-form input."""
    entries = _gather_pickable_entries(store)
    if not entries:
        print(_dim("  (no existing merchants or accounts yet)"))
        return None

    print()
    print(_bold("  Existing entries:"))
    for i, e in enumerate(entries, start=1):
        tag = _dim("[m]") if e["kind"] == "merchant" else _yellow("[a]")
        print(f"  {i:>3}. {tag} {e['alias']}")
    print()

    while True:
        raw = input(_cyan("  Pick a number, or Enter to go back: ")).strip()
        if not raw:
            return None
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(entries):
                return entries[n - 1]["alias"]
            print(f"  Out of range (1..{len(entries)})")
            continue
        # Any non-digit input is treated as a free-form alias entry.
        return raw


def _prompt_alias_with_picker(
    prompt: str, store: "ConfigStore", default: Optional[str] = None
) -> str:
    """Same contract as _prompt_alias_required, but '?' opens an interactive
    picker over existing merchants and accounts."""
    while True:
        if default:
            shown = f"{prompt} (default: '{default}', '?' to list): "
        else:
            shown = f"{prompt} ('?' to list): "
        raw = input(shown).strip()

        if raw == "?":
            picked = _interactive_pick(store)
            if picked is not None:
                return picked
            continue

        if raw:
            return raw
        if default:
            return default
        print("Alias is required. Please enter a value, or '?' to choose.")


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


def _link_account_transfer_payee(
    store: ConfigStore,
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
    store: ConfigStore,
    ynab_client: YNABClient,
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


def _account_with_overrides(fw_acc, name: str, balance: int):
    """Return a shallow copy of fw_acc with name and balance overridden,
    suitable to pass to ynab_client.create_account."""
    import copy
    copy_acc = copy.copy(fw_acc)
    copy_acc.name = name
    copy_acc.balance = balance
    return copy_acc


def _reconcile_store_accounts_to_ynab(
    store: ConfigStore,
    ynab_accounts,
    ynab_client: YNABClient,
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
            print(f"  Recreated YNAB account '{name}'")
            created += 1
        except Exception as e:
            print(f"  Failed to create YNAB account '{name}': {e}")
    return created


def _reconcile_store_merchants_to_ynab(
    store: ConfigStore,
    ynab_payees,
    ynab_client: YNABClient,
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
            print(f"  Skipping merchant {entry.get('id')!r}: no alias/name to push")
            continue
        try:
            new_payee = ynab_client.create_payee(budget_id, name)
            store.set_merchant_ynab_record(entry["id"], to_dict(new_payee))
            print(f"  Recreated YNAB payee '{name}'")
            created += 1
        except Exception as e:
            print(f"  Failed to create YNAB payee '{name}': {e}")
    return created


def sync_accounts(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
):
    """Phase 1: ensure every FinWise account has a matching entity in the store
    and a corresponding YNAB account, creating new YNAB accounts when needed."""
    print("\n--- Account Sync ---")

    print("Fetching FinWise accounts...")
    try:
        fw_accounts = fw_client.get_accounts()
        print(f"  Fetched {len(fw_accounts)} FinWise accounts")
    except Exception as e:
        print(f"Failed to fetch FinWise accounts: {e}")
        return

    print("Fetching YNAB accounts...")
    try:
        ynab_accounts = ynab_client.get_accounts(budget_id)
        print(f"  Fetched {len(ynab_accounts)} YNAB accounts")
    except Exception as e:
        print(f"Failed to fetch YNAB accounts: {e}")
        return

    print("Refreshing nested records...")
    store.refresh_records(fw_accounts=fw_accounts, ynab_accounts=ynab_accounts)

    # Reconcile: push any store accounts that lack a valid YNAB-side record
    # into YNAB. After this, every store account has a live YNAB counterpart.
    print("Reconciling store accounts -> YNAB...")
    pushed = _reconcile_store_accounts_to_ynab(
        store, ynab_accounts, ynab_client, budget_id
    )
    if pushed:
        # Refetch so subsequent name-matching sees the newly created accounts.
        try:
            ynab_accounts = ynab_client.get_accounts(budget_id)
        except Exception as e:
            print(f"Warning: failed to refetch YNAB accounts after reconcile: {e}")

    ynab_by_name = {normalize_alias(a.name): a for a in ynab_accounts}

    for fw_acc in fw_accounts:
        if store.account_by_finwise_id(fw_acc.finwise_id):
            continue

        alias = _prompt_alias_required(
            f"Enter YNAB account name for FinWise account '{fw_acc.name}'",
            default=fw_acc.name,
        )

        match = ynab_by_name.get(normalize_alias(alias))
        if match:
            fw_record = {
                "id": fw_acc.finwise_id,
                "name": fw_acc.name,
                "type": fw_acc.type,
                "balance": fw_acc.balance,
                "currency_code": fw_acc.currency_code,
            }
            ynab_record = {
                "id": match.ynab_id,
                "name": match.name,
                "type": match.type,
                "balance": match.balance,
                "transfer_payee_id": match.transfer_payee_id,
            }
            store.add_account(alias=alias, fw_record=fw_record, ynab_record=ynab_record)
            print(f"Linked '{fw_acc.name}' -> existing YNAB account '{match.name}'")
            continue

        # Create on YNAB side
        try:
            starting_balance = _calculate_starting_balance(fw_acc, fw_client)
            fw_acc_for_create = _account_with_overrides(
                fw_acc, name=alias, balance=starting_balance
            )
            response = ynab_client.create_account(budget_id, fw_acc_for_create)
            new_record = response.data.account
            fw_record = {
                "id": fw_acc.finwise_id,
                "name": fw_acc.name,
                "type": fw_acc.type,
                "balance": fw_acc.balance,
                "currency_code": fw_acc.currency_code,
            }
            store.add_account(
                alias=alias,
                fw_record=fw_record,
                ynab_record=to_dict(new_record),
            )
            print(f"Created YNAB account '{alias}'")
        except Exception as e:
            print(f"Failed to create YNAB account '{alias}': {e}")
            continue


def sync_merchants(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
):
    """Phase 2: ensure every distinct FinWise merchant has a matching entity
    in the store and a corresponding YNAB payee, creating new YNAB payees
    when needed."""
    print("\n--- Merchant Sync ---")

    print("Fetching FinWise transactions...")
    try:
        fw_transactions = fw_client.get_transactions()
        print(f"  Fetched {len(fw_transactions)} FinWise transactions")
    except Exception as e:
        print(f"Failed to fetch FinWise transactions: {e}")
        return

    print("Fetching YNAB payees...")
    try:
        ynab_payees = ynab_client.get_payees(budget_id)
        print(f"  Fetched {len(ynab_payees)} YNAB payees")
    except Exception as e:
        print(f"Failed to fetch YNAB payees: {e}")
        return

    print("Refreshing nested records...")
    store.refresh_records(ynab_payees=ynab_payees)

    # Reconcile: push any store merchants whose YNAB payee no longer exists
    # (or whose ynab record is empty) into YNAB.
    print("Reconciling store merchants -> YNAB...")
    pushed = _reconcile_store_merchants_to_ynab(
        store, ynab_payees, ynab_client, budget_id
    )
    if pushed:
        try:
            ynab_payees = ynab_client.get_payees(budget_id)
        except Exception as e:
            print(f"Warning: failed to refetch YNAB payees after reconcile: {e}")

    def _account_is_ignored(fw_account_id):
        acc = store.account_by_finwise_id(fw_account_id)
        return bool(acc and acc.get("ignore_transactions"))

    fw_transactions = [
        t for t in fw_transactions
        if not _account_is_ignored(getattr(t, "account_id", None))
    ]

    fw_merchants = _extract_distinct_merchants(fw_transactions)
    unknown = [m for m in fw_merchants if not store.merchant_by_finwise_id(m["id"])]
    total = len(unknown)
    print(
        f"Distinct FinWise merchants in period: {len(fw_merchants)} "
        f"({_yellow(str(total))} new)"
    )

    ynab_by_name = {normalize_alias(p.name): p for p in ynab_payees}

    for idx, fw_m in enumerate(unknown, start=1):
        # Re-check inside the loop in case a previous iteration's
        # attach_finwise_to_merchant absorbed this merchant via alias dedup.
        if store.merchant_by_finwise_id(fw_m["id"]):
            continue

        # Header bar with progress counter
        header = f" Merchant {idx} of {total} "
        bar = "━" * max(0, 60 - len(header))
        print(f"\n{_cyan('━━━')}{_bold(_cyan(header))}{_cyan(bar)}")

        print(f"  {_dim('ID  ')} {_yellow(fw_m['id'])}")
        print(f"  {_dim('Name')} {fw_m.get('name') or _dim('(none)')}")

        samples = fw_m.get("samples", [])
        if samples:
            print(f"  {_dim('Date         Amount      Description')}")
            for s in samples:
                date_str = s.get("date") or "?"
                if s.get("amount") is not None:
                    amt = s["amount"]
                    raw = f"{amt:>10.2f}"
                    amount_str = _red(raw) if amt < 0 else _green(raw)
                else:
                    amount_str = f"{'?':>10}"
                desc = s.get("description") or _dim("(no description)")
                print(f"  {date_str}   {amount_str}   {desc}")
                orig = s.get("original_description")
                if orig:
                    print(f"  {' ' * 24}{_dim('└─ ' + orig)}")

        alias = _prompt_alias_with_picker(
            _cyan("  → YNAB payee name"),
            store,
            default=fw_m.get("name") or None,
        )

        existing = store.merchant_by_alias(alias)
        if existing:
            store.attach_finwise_to_merchant(existing["id"], fw_m)
            print(
                f"Attached FinWise merchant '{fw_m['id']}' to existing "
                f"'{existing['alias']}'"
            )
            continue

        # Alias matches a stored account? Link to that account's transfer
        # payee instead of creating a duplicate regular payee.
        if _link_account_transfer_payee(store, ynab_payees, alias, fw_m):
            print(f"Linked merchant '{alias}' -> transfer payee for own account")
            continue

        ynab_match = ynab_by_name.get(normalize_alias(alias))
        if ynab_match:
            store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(ynab_match),
            )
            print(f"Linked merchant '{alias}' -> existing YNAB payee")
            continue

        try:
            new_payee = ynab_client.create_payee(budget_id, alias)
            store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(new_payee),
            )
            print(f"Created YNAB payee '{alias}'")
        except Exception as e:
            print(f"Failed to create YNAB payee '{alias}': {e}")
            continue


def main():
    load_dotenv()

    import sys

    print("Hello from finab!")

    # Use the wrapper instead of raw FinWise client
    fw_client = FinWiseClient()

    # Initialize YNAB Client
    try:
        ynab_client = YNABClient()
    except Exception as e:
        print(f"Failed to initialize YNAB Client: {e}")
        return

    try:
        print("\nFetching budgets via YNABClient...")
        budgets = ynab_client.get_budgets()

        if not budgets:
            print("No budgets found in YNAB.")
            return

        budget_id = load_budget_id()

        # Verify stored budget_id is valid
        if budget_id:
            found_budget = next((b for b in budgets if str(b.id) == budget_id), None)
            if found_budget:
                print(f"Using stored budget: {found_budget.name}")
            else:
                print(
                    f"Stored budget ID {budget_id} not found in YNAB. Please re-select."
                )
                budget_id = None

        if not budget_id:
            if len(budgets) == 1:
                budget_id = str(budgets[0].id)
                print(f"Using budget: {budgets[0].name}")
            else:
                print("\nMultiple budgets found. Please select one:")
                for i, budget in enumerate(budgets):
                    print(f"{i + 1}. {budget.name} (ID: {budget.id})")

                while True:
                    try:
                        selection = input("\nEnter the number of the budget to use: ")
                        index = int(selection) - 1
                        if 0 <= index < len(budgets):
                            budget_id = str(budgets[index].id)
                            print(f"Selected budget: {budgets[index].name}")
                            break
                        else:
                            print("Invalid selection. Please try again.")
                    except ValueError:
                        print("Invalid input. Please enter a number.")

            # Save the new selection
            if budget_id:
                save_budget_id(budget_id)

        if budget_id:
            store = ConfigStore()

            # Phase 1
            sync_accounts(fw_client, ynab_client, budget_id, store)

            # Phase 2
            sync_merchants(fw_client, ynab_client, budget_id, store)

            # Phase 3 (new transaction pipeline)
            sync_transactions(fw_client, ynab_client, budget_id, store)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
