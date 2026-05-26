from datetime import date
from typing import Optional
import re
import sys
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.models import (
    YNABTransaction,
    Transaction,
)  # Import needed for Starting Balance txn
from finab.config import (
    load_aliases,
    load_payee_rules,
    save_payee_rules,
    load_merchant_aliases,
    load_category_rules,
    save_category_rules,
    load_budget_id,
    save_budget_id,
    load_import_id_offset,
    load_cache,
    save_cache,
    clear_cache,
)
from finab.store import ConfigStore, to_dict, normalize_alias
import hashlib


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


def normalize_payee_for_matching(payee_name: Optional[str]) -> str:
    """
    Normalize payee name for transaction matching.

    Converts to lowercase and truncates to 50 characters (YNAB's limit).
    This ensures consistent matching between FinWise and YNAB transactions.

    Args:
        payee_name: The payee name to normalize, or None

    Returns:
        Normalized payee name string (empty string if None)
    """
    if not payee_name:
        return ""
    # Truncate to 50 chars (YNAB's limit) and convert to lowercase
    normalized = payee_name[:50] if len(payee_name) > 50 else payee_name
    return normalized.lower()


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


def generate_import_id(original_id: str, offset: str) -> str:
    """
    Generate a deterministic, unique import_id for YNAB.

    Concatenates the original_id and offset, then generates a SHA-256 hash.
    Returns the first 36 characters of the hexdigest to stay within YNAB's limit.

    Args:
        original_id: The original transaction ID from FinWise
        offset: The offset string used for hashing

    Returns:
        A 36-character hashed import_id
    """
    combined = original_id + offset
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:36]


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

    try:
        ynab_payees = ynab_client.get_payees(budget_id)
    except Exception:
        ynab_payees = []

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
    try:
        new_payee = ynab_client.create_payee(budget_id, alias)
        store.add_merchant(
            alias=alias,
            fw_record=fw_record,
            ynab_record=to_dict(new_payee),
        )
    except Exception as e:
        print(f"Failed to create YNAB payee '{alias}': {e}")
        # Fall back to an empty ynab record so the FW id is at least
        # captured; user can resolve via a Phase 2 re-run.
        store.add_merchant(alias=alias, fw_record=fw_record, ynab_record={})


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

    try:
        fw_accounts = fw_client.get_accounts()
        print(f"FinWise Accounts: {len(fw_accounts)}")
    except Exception as e:
        print(f"Failed to fetch FinWise accounts: {e}")
        return

    try:
        ynab_accounts = ynab_client.get_accounts(budget_id)
        print(f"YNAB Accounts: {len(ynab_accounts)}")
    except Exception as e:
        print(f"Failed to fetch YNAB accounts: {e}")
        return

    store.refresh_records(fw_accounts=fw_accounts, ynab_accounts=ynab_accounts)

    # Reconcile: push any store accounts that lack a valid YNAB-side record
    # into YNAB. After this, every store account has a live YNAB counterpart.
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

    start_date = date.today().replace(day=1)

    try:
        fw_transactions = fw_client.get_transactions(start_date=start_date)
    except Exception as e:
        print(f"Failed to fetch FinWise transactions: {e}")
        return

    try:
        ynab_payees = ynab_client.get_payees(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB payees: {e}")
        return

    store.refresh_records(ynab_payees=ynab_payees)

    # Reconcile: push any store merchants whose YNAB payee no longer exists
    # (or whose ynab record is empty) into YNAB.
    pushed = _reconcile_store_merchants_to_ynab(
        store, ynab_payees, ynab_client, budget_id
    )
    if pushed:
        try:
            ynab_payees = ynab_client.get_payees(budget_id)
        except Exception as e:
            print(f"Warning: failed to refetch YNAB payees after reconcile: {e}")

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


def map_accounts(
    finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str
):
    """Maps FinWise accounts to YNAB accounts."""
    print("Mapping accounts...")
    try:
        fw_accounts = finwise_client.get_accounts()
        ynab_accounts = ynab_client.get_accounts(budget_id)
        account_aliases = load_aliases()

        ynab_map_by_name = {
            acc.name: acc.ynab_id for acc in ynab_accounts if acc.ynab_id
        }

        # account_id_to_name maps YNAB account ID -> FinWise account name. We key
        # by YNAB ID because merge_and_filter_transactions rewrites each
        # transaction's account_id from the FinWise ID to the YNAB ID before the
        # display code in process_payee_aliases / process_categories runs.
        fw_id_to_ynab_id = {}
        account_id_to_name = {}
        for fw_acc in fw_accounts:
            if not fw_acc.finwise_id:
                continue
            lookup_name = account_aliases.get(fw_acc.name, fw_acc.name)
            if lookup_name in ynab_map_by_name:
                ynab_id = ynab_map_by_name[lookup_name]
                fw_id_to_ynab_id[fw_acc.finwise_id] = ynab_id
                account_id_to_name[ynab_id] = fw_acc.name
            else:
                print(
                    f"Warning: Account '{fw_acc.name}' (mapped as '{lookup_name}') found in FinWise but not mapped to YNAB. Transactions for this account will be skipped."
                )

        if not fw_id_to_ynab_id:
            print("No accounts mapped. Aborting transaction sync.")
            return None, None, None

        return fw_id_to_ynab_id, account_id_to_name, ynab_accounts

    except Exception as e:
        print(f"Failed to map accounts: {e}")
        return None, None, None


def fetch_transactions(
    finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str
):
    """Fetches transactions from both services."""
    print("Fetching transactions...")
    try:
        # Fetch last 30 days by default or maybe year to date? User didn't specify, let's do YTD for now based on main() default
        end_date = None  # date.today()
        start_date = date.today().replace(day=1)

        fw_transactions = finwise_client.get_transactions(
            start_date=start_date, end_date=end_date
        )
        ynab_transactions = ynab_client.get_transactions(
            budget_id, start_date=start_date
        )  # YNAB API handles start_date

        print(f"FinWise Transactions: {len(fw_transactions)}")
        print(f"YNAB Transactions: {len(ynab_transactions)}")

        return fw_transactions, ynab_transactions

    except Exception as e:
        print(f"Failed to fetch transactions: {e}")
        return None, None


def process_payee_aliases(
    fw_transactions, ynab_client, budget_id, account_id_to_name, ynab_accounts, store: ConfigStore
):
    """Applies payee aliasing rules and prompts user for unknowns."""
    print("Processing payee aliases...")
    payee_rules = load_payee_rules()
    merchant_aliases = load_merchant_aliases(store=store)

    # Build account name -> transfer_payee_id map for auto-transfer detection
    account_transfer_map = {
        acc.name.lower(): acc.transfer_payee_id
        for acc in ynab_accounts
        if acc.transfer_payee_id
    }

    def apply_transfer_if_account(txn, payee_name):
        """If payee_name matches a YNAB account name, treat as transfer."""
        transfer_payee_id = (
            account_transfer_map.get(payee_name.lower()) if payee_name else None
        )
        if transfer_payee_id:
            txn.payee_id = transfer_payee_id
            txn.payee_name = None
            txn.category_id = None
            print(f"Auto-transfer: '{payee_name}' matches account, set as transfer.")
            return True
        return False

    # Fetch existing YNAB payees to avoid prompting for known ones
    try:
        ynab_payees_list = ynab_client.get_payees(budget_id)
        ynab_payee_names = {p.name for p in ynab_payees_list}
    except Exception as e:
        print(f"Failed to fetch payees: {e}")
        ynab_payee_names = set()

    session_ignored_payees = set()
    session_ignored_merchants = set()
    rules_modified = False

    for fw_txn in fw_transactions:
        # FinWiseTransaction uses 'description' as payee_name and memo in 'from_finwise'
        # Transaction model has 'payee_name'
        original_payee = fw_txn.payee_name

        # Priority 1: Merchant ID Aliasing
        if fw_txn.merchant_id:
            if fw_txn.merchant_id in merchant_aliases:
                resolved = merchant_aliases[fw_txn.merchant_id]
                if apply_transfer_if_account(fw_txn, resolved):
                    continue
                fw_txn.payee_name = resolved
                continue
            if fw_txn.merchant_id in session_ignored_merchants:
                continue

            # Check if we should prompt for this merchant ID
            # If the original payee (description) is already a known YNAB payee,
            # we might want to skip, BUT since the user wants to map IDs explicitly,
            # maybe we should check if the ID is unmapped first.

            # Let's check if the description matches a regex rule first as a fallback/helper?
            # No, requirement says "map merchant_id to a payee alias".

            # However, if the description exactly matches a YNAB payee, maybe we don't need an alias?
            # But the user said "merchant_id is unreadable", so relying on description
            # matching YNAB payee might be flaky if the description changes.
            # Let's prompt if ID is unknown, but maybe offer the description as default.

            # Get account name for display
            account_name = account_id_to_name.get(fw_txn.account_id, "Unknown Account")
            amount_val = fw_txn.amount / 1000.0

            print(f"\nAccount: '{account_name}' | Amount: {amount_val:.2f}")
            print(f"Merchant ID: {fw_txn.merchant_id}")
            print(f"Merchant Name: {fw_txn.merchant_name}")
            print(f"Description: {original_payee}")

            target = input(
                f"Enter YNAB Payee for this merchant (or Press Enter to skip/ignore): "
            ).strip()

            if target:
                _record_merchant_alias(
                    store,
                    ynab_client,
                    budget_id,
                    fw_merchant_id=fw_txn.merchant_id,
                    alias=target,
                    fw_merchant_name=getattr(fw_txn, "merchant_name", None),
                )
                # Update local dict so subsequent transactions in this session see it
                merchant_aliases[fw_txn.merchant_id] = target

                print(f"Mapping saved: {fw_txn.merchant_id} -> {target}")
                if apply_transfer_if_account(fw_txn, target):
                    continue
                fw_txn.payee_name = target
                ynab_payee_names.add(target)
                continue
            else:
                session_ignored_merchants.add(fw_txn.merchant_id)
                # Fall through to regex logic or keep original?
                # If ignored, we probably just keep original description
                # or let regex logic try to clean it up.
                pass

        # Priority 2: Regex / Description Aliasing (Fallback)
        final_payee = original_payee
        matched_rule = False

        # Check existing rules (iterate through current list which includes newly added ones)
        for rule in payee_rules:
            try:
                if re.search(rule["pattern"], original_payee):
                    final_payee = rule["target"]
                    matched_rule = True
                    break
            except re.error:
                print(
                    f"Warning: Invalid regex pattern '{rule['pattern']}' in config. Skipping."
                )
                continue

        if matched_rule:
            if apply_transfer_if_account(fw_txn, final_payee):
                continue
            fw_txn.payee_name = final_payee
            continue

        # If not matched, check if exists in YNAB or ignored
        if final_payee in ynab_payee_names:
            continue

        if original_payee in session_ignored_payees:
            continue

        # Only prompt for regex alias if we haven't already prompted for Merchant ID
        # (If we prompted for Merchant ID and they skipped, we might still fall here.
        # That's okay, maybe they want to regex map the description instead.)

        # Get account name for display
        account_name = account_id_to_name.get(fw_txn.account_id, "Unknown Account")

        # Format amount
        amount_val = fw_txn.amount / 1000.0

        # Prompt user
        print(
            f"\nAccount: '{account_name}' | Amount: {amount_val:.2f} | Unknown Payee: '{original_payee}'"
        )

        while True:
            target = input(
                f"Enter alias for '{original_payee}' (or Press Enter to keep as-is): "
            ).strip()

            if not target:
                session_ignored_payees.add(original_payee)
                break

            pattern_input = input(
                f"Enter search term to match for alias '{target}' (default: matches '{original_payee}'): "
            ).strip()

            try:
                # Generate case-insensitive regex pattern
                if pattern_input:
                    escaped_choice = re.escape(pattern_input)
                else:
                    escaped_choice = re.escape(original_payee)

                pattern = f"(?i).*{escaped_choice}.*"

                # Validate regex (should be valid by construction, but good practice)
                re.compile(pattern)

                # Verify that the generated pattern actually matches the current payee
                if not re.search(pattern, original_payee):
                    print(
                        f"Error: The generated regex ({pattern}) does not match the current payee '{original_payee}'. Please try again."
                    )
                    continue

                new_rule = {"pattern": pattern, "target": target}
                payee_rules.append(new_rule)
                rules_modified = True
                # Save immediately to ensure safety
                save_payee_rules(payee_rules)

                print(f"Rule added: Matches '{pattern}' -> Maps to '{target}'")
                if apply_transfer_if_account(fw_txn, target):
                    break
                fw_txn.payee_name = target
                ynab_payee_names.add(
                    target
                )  # Add to known list so we don't prompt again for target
                break
            except re.error:
                print(
                    "Generated regex was invalid (unexpected). Skipping rule creation."
                )
                # Loop back to let user retry
                continue

    if rules_modified:
        save_payee_rules(payee_rules)


def merge_and_filter_transactions(
    fw_transactions, ynab_transactions, fw_id_to_ynab_id, ynab_accounts
):
    """
    Merges FinWise and YNAB transactions, returning a unified list to process.

    Returns a list of Transaction objects where:
    - FinWise transactions that match YNAB get their ynab_id set
    - Uncategorized YNAB transactions (matched or unmatched) are included
    - New FinWise transactions (no match) are included
    - Transfer transactions are excluded from processing
    """
    print("Merging and filtering transactions...")

    offset = load_import_id_offset()

    # Build set of transfer payee IDs for transfer detection
    transfer_payee_ids = {
        acc.transfer_payee_id for acc in ynab_accounts if acc.transfer_payee_id
    }

    # Build lookup maps for YNAB transactions
    ynab_by_import_id = {}
    # Fallback map for migration: (date, amount, normalized_payee) -> list of transactions
    ynab_by_fuzzy_key = {}

    for txn in ynab_transactions:
        # Add to import_id map
        if txn.import_id:
            ynab_by_import_id[txn.import_id] = txn

        # Add to fuzzy key map for migration fallback
        fuzzy_key = (txn.var_date, txn.amount, normalize_payee_for_matching(txn.payee_name))
        if fuzzy_key not in ynab_by_fuzzy_key:
            ynab_by_fuzzy_key[fuzzy_key] = []
        ynab_by_fuzzy_key[fuzzy_key].append(txn)

    transactions_to_process = []
    matched_ynab_ids = set()

    # Process FinWise transactions
    for fw_txn in fw_transactions:
        # Skip if account is not mapped
        if fw_txn.account_id not in fw_id_to_ynab_id:
            continue

        # Update to YNAB account ID
        ynab_account_id = fw_id_to_ynab_id[fw_txn.account_id]

        # Generate hashed import_id using the new helper
        hashed_id = generate_import_id(fw_txn.import_id, offset)

        # Try to match by hashed import_id first
        ynab_txn = None
        if hashed_id in ynab_by_import_id:
            candidate = ynab_by_import_id[hashed_id]
            if candidate.id not in matched_ynab_ids:
                ynab_txn = candidate

        # Fallback: Match by Date + Amount + Payee for migration from old format
        if not ynab_txn:
            fuzzy_key = (
                fw_txn.date,
                fw_txn.amount,
                normalize_payee_for_matching(fw_txn.payee_name),
            )
            if fuzzy_key in ynab_by_fuzzy_key:
                for candidate in ynab_by_fuzzy_key[fuzzy_key]:
                    if candidate.id not in matched_ynab_ids:
                        ynab_txn = candidate
                        print(
                            f"Migration match (fuzzy): {fw_txn.payee_name} ({fw_txn.amount / 1000:.2f})"
                        )
                        break

        fw_txn.import_id = hashed_id

        # If we found a match, process it
        if ynab_txn:
            matched_ynab_ids.add(ynab_txn.id)

            # Link them and copy category_id if it exists
            fw_txn.ynab_id = ynab_txn.id
            fw_txn.category_id = ynab_txn.category_id

            # If YNAB transaction is already categorized, skip it
            # We don't want to re-process or re-sync transactions that are already properly categorized
            # This prevents overwriting user's manual categorization in YNAB
            if ynab_txn.category_id:
                print(
                    f"Skipping already categorized: {ynab_txn.payee_name} ({ynab_txn.amount / 1000:.2f})"
                )
                continue  # Skip adding to transactions_to_process

            if ynab_txn.transfer_account_id:
                continue

            # Skip deleted transactions
            if ynab_txn.deleted:
                continue

            # Otherwise, add to processing list (uncategorized match)
            print(
                f"Found uncategorized match: {fw_txn.payee_name} ({fw_txn.amount / 1000:.2f})"
            )

        # Update account ID and add to list
        fw_txn.account_id = ynab_account_id
        # Don't overwrite payee_name - it should be preserved from FinWise or from aliasing
        transactions_to_process.append(fw_txn)

    print(f"Total transactions to process: {len(transactions_to_process)}")
    for t in transactions_to_process:
        print(t.payee_name)
    return transactions_to_process


def _build_cache(transactions):
    """Serialize per-transaction decisions to a cache-friendly dict, keyed by
    import_id. Only transactions with a recorded decision are included."""
    cache = {}
    for txn in transactions:
        if not txn.import_id:
            continue
        entry = {}
        if txn.category_id is not None:
            entry["category_id"] = txn.category_id
        if txn.subtransactions:
            entry["subtransactions"] = txn.subtransactions
        if txn.payee_id is not None:
            entry["payee_id"] = txn.payee_id
            entry["payee_name"] = txn.payee_name
        if entry:
            cache[txn.import_id] = entry
    return cache


def _apply_cache(transactions):
    """Restore prior decisions from cache.json onto the current transaction
    list. Runs after process_payee_aliases so cached transfer markings
    (payee_name=None) survive alias re-application."""
    cache = load_cache()
    if not cache:
        return
    restored = 0
    for txn in transactions:
        if not txn.import_id:
            continue
        entry = cache.get(txn.import_id)
        if not entry:
            continue
        if "category_id" in entry:
            txn.category_id = entry["category_id"]
        if "subtransactions" in entry:
            txn.subtransactions = entry["subtransactions"]
        if "payee_id" in entry:
            txn.payee_id = entry["payee_id"]
            txn.payee_name = entry.get("payee_name")
        restored += 1
    if restored:
        print(f"Restored {restored} cached decision(s) from previous run.")


def collect_split_subtransactions(fw_txn, ynab_category_map):
    """
    Walk the user through splitting a transaction across multiple categories.

    Sub-amounts use the same sign as the parent and must sum to the parent's
    amount. Returns a list of subtransaction dicts, or None if the user
    cancelled before completing the split.
    """
    total = fw_txn.amount
    sign = -1 if total < 0 else 1
    remaining = total
    subs = []

    print(f"\nSplitting transaction. Total: {total / 1000:.2f}")

    while remaining != 0:
        remaining_abs = abs(remaining) / 1000.0
        print(f"\nRemaining: {remaining_abs:.2f} ({len(subs)} split(s) so far)")

        cat_input = input(
            "Category for next split (Enter to cancel split): "
        ).strip()
        if not cat_input:
            print("Split cancelled.")
            return None

        if cat_input.lower() not in ynab_category_map:
            print(f"Category '{cat_input}' not found in YNAB.")
            continue

        amt_input = input(
            f"Amount for '{cat_input}' (positive value, Enter for remaining {remaining_abs:.2f}): "
        ).strip()

        if not amt_input:
            amount = remaining
        else:
            try:
                amount_abs = float(amt_input)
            except ValueError:
                print("Invalid amount. Please enter a number.")
                continue

            if amount_abs <= 0:
                print("Amount must be positive.")
                continue

            amount = sign * int(round(amount_abs * 1000))

            if abs(amount) > abs(remaining):
                print(
                    f"Amount {amount_abs:.2f} exceeds remaining {remaining_abs:.2f}."
                )
                continue

        subs.append(
            {
                "amount": amount,
                "category_id": ynab_category_map[cat_input.lower()],
            }
        )
        remaining -= amount
        print(f"Added split: '{cat_input}' -> {amount / 1000:.2f}")

    return subs


def process_categories(
    transactions_to_process,
    ynab_client,
    budget_id,
    account_id_to_name,
    ynab_accounts,
    store: ConfigStore,
):
    """Processes category matching and prompts user."""
    print("Processing categories...")

    category_rules = load_category_rules()
    category_rules_modified = False
    session_ignored_categories = set()

    ynab_category_map = {}
    try:
        ynab_cats = ynab_client.get_categories(budget_id)
        for c in ynab_cats:
            if not c.hidden and not c.deleted:
                ynab_category_map[c.name.lower()] = c.id
    except Exception as e:
        print(f"Failed to fetch categories: {e}")

    inflow_category_id = None
    for name in (
        "inflow: ready to assign",
        "ready to assign",
        "inflow: to be budgeted",
        "to be budgeted",
    ):
        if name in ynab_category_map:
            inflow_category_id = ynab_category_map[name]
            break

    transfer_payee_ids = {
        acc.transfer_payee_id for acc in ynab_accounts if acc.transfer_payee_id
    }

    if ynab_category_map:
        for fw_txn in transactions_to_process:
            # Persist all decisions made up to this point so an abort here
            # doesn't lose work from earlier iterations.
            save_cache(_build_cache(transactions_to_process))

            # Skip transactions that already have a decision: a category, a
            # split, or a transfer marking restored from cache or set by a
            # previous step.
            if fw_txn.category_id or fw_txn.subtransactions:
                continue
            if fw_txn.payee_id in transfer_payee_ids:
                continue

            # Use original description (memo) for matching
            description = fw_txn.memo or ""

            matched_category_id = None
            confirmation_needed = False
            suggested_category_name = None

            # Check existing rules
            if description:
                for pattern, cat_name in category_rules.items():
                    try:
                        if re.search(pattern, description, re.IGNORECASE):
                            check_name = cat_name
                            is_confirm = False

                            if check_name.startswith("?"):
                                check_name = check_name[1:]
                                is_confirm = True

                            if check_name.lower() in ynab_category_map:
                                matched_category_id = ynab_category_map[check_name.lower()]
                                if is_confirm:
                                    confirmation_needed = True
                                    suggested_category_name = check_name
                                break
                            else:
                                print(
                                    f"Warning: Rule matches '{cat_name}', but category not found in YNAB."
                                )
                    except re.error:
                        continue

            if matched_category_id and not confirmation_needed:
                fw_txn.category_id = matched_category_id
                continue

            # Positive amounts are inflows. Transfers carry a transfer_payee_id and
            # must stay category-less, so skip those.
            if (
                matched_category_id is None
                and fw_txn.amount > 0
                and inflow_category_id
                and fw_txn.payee_id not in transfer_payee_ids
            ):
                fw_txn.category_id = inflow_category_id
                print(
                    f"Auto-assigned inflow: {fw_txn.payee_name} ({fw_txn.amount / 1000:.2f})"
                )
                continue

            if not description:
                continue

            if description in session_ignored_categories:
                continue

            # Prompt user
            account_name = account_id_to_name.get(fw_txn.account_id, "Unknown Account")
            amount_val = fw_txn.amount / 1000.0

            print(f"\nAccount: '{account_name}' | Amount: {amount_val:.2f}")
            print(f"Description: {description}")
            if fw_txn.original_description and fw_txn.original_description != description:
                print(f"Original Description: {fw_txn.original_description}")
            print(f"Payee: {fw_txn.payee_name}")  # Show resolved payee
            if fw_txn.merchant_id:
                print(f"Merchant ID: {fw_txn.merchant_id}")

            # Show if this is an existing YNAB transaction
            if fw_txn.ynab_id:
                print(f"[Existing YNAB Transaction - ID: {fw_txn.ynab_id}]")

            if confirmation_needed and suggested_category_name:
                print(f"Suggested Category (Confirm?): {suggested_category_name}")

            while True:
                prompt_text = "Enter Category Name (or Press Enter to skip, "
                if confirmation_needed and suggested_category_name:
                    prompt_text = f"Enter Category Name (Press Enter to confirm '{suggested_category_name}', "

                prompt_text += "'r' to reload, 'i' Inflow, 't' Transfer, 'o' One-off, 'c' Confirm-Rule, 's' Split, 'p' Payee): "

                cat_input = input(prompt_text).strip()

                if confirmation_needed and suggested_category_name and not cat_input:
                    # User confirmed suggestion
                    print(f"Category confirmed: '{suggested_category_name}'")
                    fw_txn.category_id = ynab_category_map[
                        suggested_category_name.lower()
                    ]
                    break

                if cat_input.lower() == "c" or cat_input.lower().startswith("c "):
                    # Confirm-Rule: Create a rule that requires confirmation
                    if cat_input.lower() == "c":
                        cat_input = input("Enter Category for Confirm-Rule: ").strip()
                    else:
                        cat_input = cat_input[2:].strip()

                    if not cat_input:
                        print("Cancelled.")
                        continue

                    if cat_input.lower() in ynab_category_map:
                        selected_cat_id = ynab_category_map[cat_input.lower()]

                        search_term = input(
                            f"Enter text to match for '{cat_input}' (default: '{description}'): "
                        ).strip()
                        if not search_term:
                            search_term = description

                        escaped_term = re.escape(search_term)
                        final_regex = f"(?i).*{escaped_term}.*"

                        # Add ? prefix
                        category_rules[final_regex] = f"?{cat_input}"
                        category_rules_modified = True
                        save_category_rules(category_rules)
                        print(
                            f"Saved Confirmation Rule: '{search_term}' -> '?{cat_input}'"
                        )

                        fw_txn.category_id = selected_cat_id
                        break
                    else:
                        print(f"Category '{cat_input}' not found in YNAB.")
                        continue

                if cat_input.lower() == "o" or cat_input.lower().startswith("o "):
                    # One-off category assignment (no rule creation)
                    if cat_input.lower() == "o":
                        cat_input = input("Enter One-off Category Name: ").strip()
                    else:
                        cat_input = cat_input[2:].strip()

                    if not cat_input:
                        print("Cancelled.")
                        continue

                    if cat_input.lower() in ynab_category_map:
                        selected_cat_id = ynab_category_map[cat_input.lower()]
                        fw_txn.category_id = selected_cat_id
                        print(
                            f"One-off category set to '{cat_input}' (No rule created)"
                        )
                        break
                    else:
                        print(f"Category '{cat_input}' not found in YNAB.")
                        continue

                if cat_input.lower() == "s":
                    # Split across multiple categories (no rule creation)
                    subs = collect_split_subtransactions(fw_txn, ynab_category_map)
                    if subs:
                        fw_txn.subtransactions = subs
                        fw_txn.category_id = None
                        print(
                            f"Transaction split into {len(subs)} sub-transaction(s)."
                        )
                        break
                    continue

                if cat_input.lower() == "p":
                    # Assign a payee name to this transaction's merchant_id
                    if not fw_txn.merchant_id:
                        print(
                            "No merchant_id on this transaction; cannot save a merchant alias."
                        )
                        continue

                    new_payee = input(
                        f"Enter payee name for merchant_id '{fw_txn.merchant_id}' (Enter to cancel): "
                    ).strip()
                    if not new_payee:
                        print("Cancelled.")
                        continue

                    _record_merchant_alias(
                        store,
                        ynab_client,
                        budget_id,
                        fw_merchant_id=fw_txn.merchant_id,
                        alias=new_payee,
                        fw_merchant_name=getattr(fw_txn, "merchant_name", None),
                    )

                    fw_txn.payee_name = new_payee
                    # Clear any prior transfer marking so the new payee sticks
                    fw_txn.payee_id = None
                    print(
                        f"Merchant alias saved: {fw_txn.merchant_id} -> '{new_payee}'"
                    )
                    print(f"Payee: {fw_txn.payee_name}")
                    continue

                if cat_input.lower() == "t":
                    # Transfer handling
                    while True:
                        t_account_name = input("Enter Transfer Account Name: ").strip()
                        if not t_account_name:
                            print("Transfer cancelled.")
                            break

                        # Find account by name (case-insensitive)
                        found_acc = next(
                            (
                                a
                                for a in ynab_accounts
                                if a.name.lower() == t_account_name.lower()
                            ),
                            None,
                        )

                        if found_acc:
                            # Set payee_id to the transfer_payee_id and category to None
                            if found_acc.transfer_payee_id:
                                fw_txn.payee_id = found_acc.transfer_payee_id
                                fw_txn.category_id = None
                                fw_txn.payee_name = None
                                print(f"Transfer set to account: '{found_acc.name}'")
                                break  # Break inner loop
                            else:
                                print(
                                    f"Error: Account '{found_acc.name}' does not have a valid transfer_payee_id."
                                )
                        else:
                            print(f"Account '{t_account_name}' not found in YNAB.")

                    if fw_txn.payee_id:
                        break  # Break outer loop if transfer payee set

                if cat_input.lower() == "i":
                    # Special handling for Inflow
                    candidates = [
                        "inflow: ready to assign",
                        "ready to assign",
                        "inflow: to be budgeted",
                        "to be budgeted",
                    ]
                    for c in candidates:
                        if c in ynab_category_map:
                            cat_input = c
                            print(f"Selected special category: '{c}'")
                            break
                    else:
                        print(
                            "Could not find 'Inflow: Ready to Assign' category in YNAB."
                        )
                        continue

                if cat_input.lower() == "r":
                    print("Reloading YNAB categories...")
                    try:
                        ynab_cats = ynab_client.get_categories(budget_id)
                        ynab_category_map = {}
                        for c in ynab_cats:
                            if not c.hidden and not c.deleted:
                                ynab_category_map[c.name.lower()] = c.id
                        print(f"Categories reloaded. Total: {len(ynab_category_map)}")
                    except Exception as e:
                        print(f"Failed to reload categories: {e}")
                    continue

                if not cat_input:
                    session_ignored_categories.add(description)
                    break

                # Check if valid category
                if cat_input.lower() not in ynab_category_map:
                    print(
                        f"Category '{cat_input}' not found in YNAB. Please try again."
                    )
                    continue

                selected_cat_id = ynab_category_map[cat_input.lower()]

                # Ask for search term to automate regex creation
                search_term = input(
                    f"Enter text to match for '{cat_input}' (default: '{description}'): "
                ).strip()

                if not search_term:
                    search_term = description

                # Automate regex: Case insensitive, match anywhere, escape user input
                escaped_term = re.escape(search_term)
                final_regex = f"(?i).*{escaped_term}.*"

                try:
                    re.compile(final_regex)
                    if not re.search(final_regex, description, re.IGNORECASE):
                        print(
                            f"Error: Generated rule matches '{search_term}' but that text was not found in description '{description}'."
                        )
                        continue

                    category_rules[final_regex] = cat_input
                    category_rules_modified = True
                    save_category_rules(category_rules)

                    fw_txn.category_id = selected_cat_id
                    print(f"Category set to '{cat_input}'")
                    break
                except re.error:
                    print("Invalid regex. Try again.")
                    continue

    if category_rules_modified:
        save_category_rules(category_rules)


def sync_changes_to_ynab(transactions_to_sync, ynab_client, budget_id, ynab_accounts):
    """Filters duplicates and syncs changes (creates or updates) to YNAB.

    Returns True if every attempted YNAB call succeeded (or there was nothing
    to do); False if any create or update raised. The caller uses this to
    decide whether the cache can be cleared.
    """
    print("Syncing changes to YNAB...")

    # Build set of valid YNAB account IDs for validation
    valid_account_ids = {acc.ynab_id for acc in ynab_accounts if acc.ynab_id}

    transactions_to_create = []
    transactions_to_update = []

    for txn in transactions_to_sync:
        # txn is Transaction model

        # Validate account_id before processing
        if txn.account_id not in valid_account_ids:
            print(
                f"Warning: Skipping transaction with invalid account_id: {txn.account_id}"
            )
            continue

        # Check if it's an update or create
        if txn.ynab_id:
            # Update if a category was assigned or if the user split it into
            # multiple sub-categories (parent category stays None for splits).
            if txn.category_id or txn.subtransactions:
                transactions_to_update.append(txn)
        else:
            # It's a create
            transactions_to_create.append(txn)

    create_ok = True
    update_ok = True

    # 1. Create Transactions
    if transactions_to_create:
        print(f"Creating {len(transactions_to_create)} new transactions...")
        try:
            ynab_client.create_transactions(budget_id, transactions_to_create)
            print("Successfully created transactions.")
        except Exception as e:
            print(f"Failed to create transactions: {e}")
            create_ok = False

    # 2. Update Transactions
    if transactions_to_update:
        print(f"Updating {len(transactions_to_update)} existing transactions...")
        try:
            ynab_client.update_transactions(budget_id, transactions_to_update)
            print("Successfully updated transactions.")
        except Exception as e:
            print(f"Failed to update transactions: {e}")
            update_ok = False

    if not transactions_to_create and not transactions_to_update:
        print("No changes to sync.")

    return create_ok and update_ok


def sync_transactions(
    finwise_client: FinWiseClient, ynab_client: YNABClient, budget_id: str, store: ConfigStore
):
    print("\n--- Transaction Sync ---")

    fw_id_to_ynab_id, account_id_to_name, ynab_accounts = map_accounts(
        finwise_client, ynab_client, budget_id
    )
    if not fw_id_to_ynab_id:
        return

    fw_transactions, ynab_transactions = fetch_transactions(
        finwise_client, ynab_client, budget_id
    )
    if fw_transactions is None:
        return

    # Merge and filter transactions to get a unified list to process
    transactions_to_process = merge_and_filter_transactions(
        fw_transactions, ynab_transactions, fw_id_to_ynab_id, ynab_accounts
    )

    # Process payee aliases (only for FinWise transactions, not YNAB-only ones)
    # FinWise transactions have import_id set
    finwise_transactions = [t for t in transactions_to_process if t.import_id]
    if finwise_transactions:
        process_payee_aliases(
            finwise_transactions, ynab_client, budget_id, account_id_to_name, ynab_accounts, store
        )

    # Restore in-progress decisions from a previous (possibly aborted) run.
    # Applied after aliases so cached transfer markings are not clobbered.
    _apply_cache(transactions_to_process)

    # Process categories for all transactions
    process_categories(
        transactions_to_process,
        ynab_client,
        budget_id,
        account_id_to_name,
        ynab_accounts,
        store,
    )

    # Capture the final iteration's decision before pushing to YNAB.
    save_cache(_build_cache(transactions_to_process))

    # Sync changes to YNAB (create or update)
    if sync_changes_to_ynab(
        transactions_to_process, ynab_client, budget_id, ynab_accounts
    ):
        clear_cache()


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

            # Phase 3 (existing transaction pipeline, unchanged)
            sync_transactions(fw_client, ynab_client, budget_id, store)

    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
