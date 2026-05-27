"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
import json
import os
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore


TRANSACTIONS_FILE = Path("transactions.json")


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

    def __init__(self, path: Path = TRANSACTIONS_FILE):
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


def _is_before_current_month(txn, today: Optional[date] = None) -> bool:
    """True iff txn.date is before the first of the current month. Used to
    skip interactive categorization for older transactions (which we still
    push, just without a category)."""
    if today is None:
        today = date.today()
    start_of_month = today.replace(day=1)
    txn_date = getattr(txn, "date", None)
    if not isinstance(txn_date, date):
        return False
    return txn_date < start_of_month


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
            return str(c.id)
    return None


def merge_and_filter_transactions(
    fw_transactions,
    ynab_transactions,
    store: ConfigStore,
    tx_store: "TransactionsStore",
) -> list:
    """Map FinWise accounts to YNAB account ids via the store, dedup against
    our stable import_id stored in transactions.json, and skip transactions
    already categorized in YNAB. Returns the list of FinWise transactions
    needing processing.

    Each FW transaction passes through one of these paths:
      1. account unknown OR ignore_transactions=True -> drop
      2. previously synced + YNAB twin exists + categorized -> drop
         (preserve user's manual categorization in YNAB)
      3. previously synced + YNAB twin exists + uncategorized -> mark for
         update (set fw_txn.ynab_id; keep stable import_id)
      4. previously synced + YNAB twin missing -> rotate (generate fresh
         uuid, overwrite stored, push as new) — handles user-side deletes
      5. never synced -> new transaction (generate uuid, record, push)
    """
    # Index live YNAB transactions by their import_id (skip deleted).
    ynab_by_import_id = {
        str(t.import_id): t
        for t in ynab_transactions
        if not getattr(t, "deleted", False) and getattr(t, "import_id", None)
    }

    # Prune any stored import_ids no longer present in YNAB.
    tx_store.prune_stale(set(ynab_by_import_id.keys()))

    out = []
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        if acc.get("ignore_transactions"):
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue

        fw_uuid = fw_txn.import_id  # FW's own UUID, set by from_finwise
        our_id = tx_store.import_id_for(fw_uuid) if fw_uuid else None

        if our_id and our_id in ynab_by_import_id:
            # Already synced and YNAB still has it.
            ynab_match = ynab_by_import_id[our_id]
            if getattr(ynab_match, "category_id", None):
                # Already categorized — preserve user's manual work.
                continue
            fw_txn.ynab_id = str(ynab_match.id)
            fw_txn.import_id = our_id  # keep stable for update push
            fw_txn.category_id = None
        else:
            # Either never synced, or YNAB-twin missing (user deleted).
            # Rotate: fresh UUID, overwrite stored, push as new.
            new_id = uuid.uuid4().hex
            tx_store.record(fw_uuid, new_id)
            fw_txn.import_id = new_id

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out


# --- Color helpers (mirror main.py; kept local to avoid cross-module imports). ---
def _color(code: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"

def _bold(s: str) -> str:   return _color("1", s)
def _dim(s: str) -> str:    return _color("2", s)
def _cyan(s: str) -> str:   return _color("36", s)
def _yellow(s: str) -> str: return _color("33", s)


def _pick_category(
    merchant: dict,
    ynab_categories: list,
    category_groups: list,
    ynab_client: YNABClient,
    budget_id: str,
) -> Optional[str]:
    """Show the per-merchant category picker. Returns the chosen YNAB
    category id, or None if the user backed out."""
    cats_used: dict = merchant.get("categories_used", {}) or {}
    # Build {category_id: category_object} for quick lookups, excluding
    # hidden/deleted.
    by_id = {
        c.id: c
        for c in ynab_categories
        if not getattr(c, "hidden", False) and not getattr(c, "deleted", False)
    }
    # Sort used categories by frequency descending.
    used_sorted = sorted(
        [(cid, cnt) for cid, cnt in cats_used.items() if cid in by_id],
        key=lambda kv: (-kv[1], by_id[kv[0]].name.lower()),
    )

    while True:
        print()
        print(f"  {_bold('Categories for')} '{merchant.get('alias', '?')}':")
        for i, (cid, cnt) in enumerate(used_sorted, start=1):
            c = by_id[cid]
            print(f"   {i}. {c.name} {_dim(f'(used {cnt}x)')}")
        print()
        print(f"   {_dim('o)')} Other category")
        print(f"   {_dim('n)')} New category")
        print(f"   {_dim('b)')} Back")
        print()

        raw = input(_cyan("  Pick: ")).strip().lower()

        if not raw:
            continue
        if raw == "b":
            return None
        if raw == "o":
            picked = _pick_category_from_full_list(category_groups)
            if picked:
                return picked
            continue
        if raw == "n":
            picked = _create_new_category(category_groups, ynab_client, budget_id)
            if picked:
                return picked
            continue
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(used_sorted):
                return used_sorted[n - 1][0]
            print(f"  Out of range (1..{len(used_sorted)})")
            continue
        print(f"  Unrecognized: {raw!r}")


def _pick_category_from_full_list(category_groups: list) -> Optional[str]:
    """Flat numbered picker over every active YNAB category, grouped by
    category group for readability. Returns the chosen category id, or
    None if the user backs out (empty input)."""
    # Flatten while preserving group order.
    flat = []
    for g in category_groups:
        for c in getattr(g, "categories", []) or []:
            if getattr(c, "hidden", False) or getattr(c, "deleted", False):
                continue
            flat.append((g, c))

    if not flat:
        print(_dim("  No categories available."))
        return None

    print()
    last_group_id = None
    for i, (g, c) in enumerate(flat, start=1):
        if g.id != last_group_id:
            print(f"  {_bold(g.name)}")
            last_group_id = g.id
        print(f"   {i:>3}. {c.name}")
    print()
    raw = input(_cyan("  Pick a number, Enter to go back: ")).strip()
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(flat):
            return str(flat[n - 1][1].id)
        print(f"  Out of range (1..{len(flat)})")
    return None


def _create_new_category(
    category_groups: list, ynab_client: YNABClient, budget_id: str
) -> Optional[str]:
    """Walk the user through creating a new category (with the option to
    also create a new group on the fly). Returns the new category's id, or
    None if cancelled.

    Side effect: appends the new category to the chosen group's `.categories`
    list (and the new group to `category_groups` if one was created), so
    later prompts in the same run see them without re-fetching from YNAB.
    """
    name = input(_cyan("  New category name (Enter to cancel): ")).strip()
    if not name:
        return None

    # Pick or create a group
    print()
    print(f"  {_bold('Target group:')}")
    for i, g in enumerate(category_groups, start=1):
        print(f"   {i:>3}. {g.name}")
    print(f"   {_dim('n)')} New group")
    print(f"   {_dim('b)')} Back")
    print()

    chosen_group = None
    while chosen_group is None:
        raw = input(_cyan("  Pick: ")).strip().lower()
        if not raw or raw == "b":
            return None
        if raw == "n":
            grp_name = input(_cyan("  New group name (Enter to cancel): ")).strip()
            if not grp_name:
                return None
            try:
                new_grp = ynab_client.create_category_group(budget_id, grp_name)
            except Exception as e:
                print(f"  Failed to create category group: {e}")
                return None
            if not hasattr(new_grp, "categories") or new_grp.categories is None:
                new_grp.categories = []
            category_groups.append(new_grp)
            chosen_group = new_grp
        elif raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(category_groups):
                chosen_group = category_groups[n - 1]
            else:
                print(f"  Out of range (1..{len(category_groups)})")
        else:
            print(f"  Unrecognized: {raw!r}")

    try:
        new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)
    except Exception as e:
        print(f"  Failed to create category: {e}")
        return None

    if chosen_group.categories is None:
        chosen_group.categories = []
    chosen_group.categories.append(new_cat)
    return str(new_cat.id)


def _prompt_memo(default: str = "") -> str:
    """Prompt for a memo. Press Enter to keep `default`. Strips whitespace."""
    if default:
        shown = f"  Memo (Enter to keep '{default}'): "
    else:
        shown = "  Memo (Enter for none): "
    raw = input(shown).strip()
    return raw if raw else default


def _collect_splits(
    txn,
    merchant: dict,
    ynab_categories: list,
    category_groups: list,
    ynab_client: YNABClient,
    budget_id: str,
) -> Optional[list]:
    """Walk the user through splitting `txn` across multiple categories.
    Returns a list of {category_id, amount_milliunits, memo} dicts summing
    exactly to txn.amount, or None if cancelled.

    Amounts use the same sign as txn.amount (typically negative for outflows).
    Each split's default amount is `remaining / splits_left`; the final
    split defaults to the exact remainder so the total reconciles.
    """
    total = txn.amount
    sign = -1 if total < 0 else 1
    abs_total = abs(total)

    raw = input(_cyan("  How many splits? [2]: ")).strip()
    n = 2
    if raw:
        try:
            n = int(raw)
        except ValueError:
            print("  Invalid count.")
            return None
    if n < 2:
        print("  Splits must be 2 or more.")
        return None

    splits = []
    remaining = abs_total
    for i in range(1, n + 1):
        splits_left = n - i + 1
        default_amt = (remaining / splits_left) if splits_left else 0
        # Show default with two decimals
        label = "remaining" if splits_left == 1 else f"{default_amt/1000:.2f}"
        amt_raw = input(_cyan(f"  Split {i} of {n} — amount [{label}]: ")).strip()
        if amt_raw:
            try:
                amt_abs = float(amt_raw)
            except ValueError:
                print("  Invalid amount.")
                return None
            if amt_abs <= 0:
                print("  Amount must be positive.")
                return None
            amt_milli = int(round(amt_abs * 1000))
            if amt_milli > remaining:
                print(f"  Exceeds remaining {remaining/1000:.2f}.")
                return None
        else:
            # Default: divide remaining by splits_left, or take remainder on the last split.
            if splits_left == 1:
                amt_milli = remaining
            else:
                amt_milli = int(round(default_amt))

        cat_id = _pick_category(merchant, ynab_categories, category_groups, ynab_client, budget_id)
        if cat_id is None:
            return None

        memo = _prompt_memo("")

        splits.append({
            "category_id": cat_id,
            "amount_milliunits": sign * amt_milli,
            "memo": memo,
        })
        remaining -= amt_milli

    # If remainder didn't quite zero out (rounding), put it on the last split.
    if remaining != 0 and splits:
        splits[-1]["amount_milliunits"] += sign * remaining

    return splits


class _PendingQueue:
    """Holds categorized-but-not-yet-pushed transactions. Flushed on demand
    via the `f` command, at end of run, or after Ctrl+C confirmation.

    Each create-txn enters the queue with its durable YNAB import_id on
    `Transaction.import_id` (assigned by `merge_and_filter_transactions`
    from `TransactionsStore`). The flush sends that import_id through to
    YNAB as-is — no transient correlators, no post-flush mapping writes.
    """

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
        on success. Raises on failure (no swallowing — see fail-loud spec).

        txn.import_id is already the durable UUID set by
        merge_and_filter_transactions; we send it through as-is.
        """
        creates_snap = list(self.creates)
        updates_snap = list(self.updates)
        if creates_snap:
            ynab_client.create_transactions(budget_id, creates_snap)
        if updates_snap:
            ynab_client.update_transactions(budget_id, updates_snap)
        self.creates.clear()
        self.updates.clear()
        return True


def _can_repeat(merchant: dict, txn) -> bool:
    """True iff merchant.processings has an entry for the current
    transaction's exact amount. (Enter-repeat fires only on exact-amount
    match.)"""
    if not merchant:
        return False
    processings = merchant.get("processings") or {}
    if not processings:
        return False
    amt = getattr(txn, "amount", None)
    if amt is None:
        return False
    return str(amt) in processings


def _apply_repeat(merchant: dict, txn) -> None:
    """Replay merchant.processings[str(txn.amount)] onto txn. Single-category
    cases set txn.category_id; multi-split cases set txn.subtransactions.
    Memos use fresh defaults (FinWise description for parent, empty for
    splits) so the user doesn't inherit stale per-transaction notes."""
    entry = merchant["processings"][str(txn.amount)]
    splits = entry.get("splits", []) or []
    if len(splits) == 1:
        txn.category_id = splits[0]["category_id"]
        txn.subtransactions = []
    else:
        txn.category_id = None
        txn.subtransactions = [
            {
                "category_id": s["category_id"],
                "amount": s["amount_milliunits"],
                "memo": "",  # fresh default per spec
            }
            for s in splits
        ]
    # Parent memo: keep whatever txn.memo already is (it's the FinWise
    # description by default after Transaction.from_finwise).


def _update_merchant_memory(store: ConfigStore, merchant: dict, txn) -> None:
    """Update the merchant's categories_used (frequency map) and
    processings (dict of {str(amount): {parent_memo, splits}}) based on
    the just-categorized transaction. Persists via
    store.set_merchant_memory.

    Stringifies category_id values before storing — pickers may return
    UUID objects (from the YNAB SDK) and JSON can't serialize UUID dict
    keys."""
    def _cid_str(value):
        return str(value) if value is not None else None

    subs = list(getattr(txn, "subtransactions", []) or [])
    if subs:
        splits = [
            {
                "category_id": _cid_str(s["category_id"]),
                "amount_milliunits": s["amount"],
                "memo": s.get("memo", "") or "",
            }
            for s in subs
        ]
    else:
        splits = [
            {
                "category_id": _cid_str(txn.category_id),
                "amount_milliunits": txn.amount,
                "memo": getattr(txn, "memo", "") or "",
            }
        ]

    counts = dict(merchant.get("categories_used", {}) or {})
    for s in splits:
        cid = s["category_id"]
        if cid:
            counts[cid] = counts.get(cid, 0) + 1

    new_entry = {
        "parent_memo": getattr(txn, "memo", "") or "",
        "splits": splits,
    }
    processings = dict(merchant.get("processings", {}) or {})
    processings[str(txn.amount)] = new_entry

    store.set_merchant_memory(
        merchant["id"],
        categories_used=counts,
        processings=processings,
    )


def _category_name(categories, category_id: str) -> Optional[str]:
    for c in categories:
        if c.id == category_id:
            return c.name
    return None


def _process_one_transaction(
    txn,
    idx: int,
    total: int,
    unflushed_count: int,
    store: ConfigStore,
    ynab_client: YNABClient,
    budget_id: str,
    ynab_categories: list,
    category_groups: list,
) -> str:
    """Drive the prompt loop for a single transaction.

    Returns one of:
      "categorized" — the transaction has been fully populated (caller
                      should enqueue it).
      "flush"       — user requested an immediate flush; caller should
                      flush the queue and then re-call this function for
                      the same transaction.
      "quit"        — user requested to quit categorizing; caller should
                      break the loop and auto-flush at end.
    """
    # --- (a) Positive amount: auto-inflow ---
    if _is_inflow(txn):
        inflow_id = _find_inflow_category(ynab_categories)
        if inflow_id:
            txn.category_id = inflow_id
            txn.subtransactions = []
            return "categorized"
        # If we couldn't find an inflow category, fall through and let the
        # user pick one manually like any other transaction.

    # --- (b) Resolve merchant ---
    merchant = None
    fw_mid = getattr(txn, "merchant_id", None)
    if fw_mid:
        merchant = store.merchant_by_finwise_id(fw_mid)

    # --- (c) Transfer: set payee, no category ---
    if _is_transfer(merchant):
        txn.payee_id = merchant["ynab"]["id"]
        txn.payee_name = None
        txn.category_id = None
        txn.subtransactions = []
        return "categorized"

    # --- (d) No merchant: push uncategorized ---
    if not merchant:
        txn.category_id = None
        txn.subtransactions = []
        return "categorized"

    # --- (d2) Pre-current-month: push with payee but no category prompt ---
    if _is_before_current_month(txn):
        txn.payee_id = merchant["ynab"].get("id")
        txn.payee_name = None
        txn.category_id = None
        txn.subtransactions = []
        return "categorized"

    # --- (e) Set payee from merchant ---
    txn.payee_id = merchant["ynab"].get("id")
    txn.payee_name = None

    # --- (f) Interactive header + prompt ---
    header = f" Transaction {idx} of {total} "
    if unflushed_count:
        header += f" ({unflushed_count} unflushed) "
    bar = "━" * max(0, 60 - len(header))
    print(f"\n{_cyan('━━━')}{_bold(_cyan(header))}{_cyan(bar)}")
    print(f"  {_dim('Merchant:')}  {merchant.get('alias', '?')}")
    print(f"  {_dim('Date:')}      {getattr(txn, 'date', '?')}")
    amount_str = f"{txn.amount / 1000:.2f}"
    print(f"  {_dim('Amount:')}    {amount_str}")
    print(f"  {_dim('Memo:')}      {getattr(txn, 'memo', '') or _dim('(none)')}")

    repeat_available = _can_repeat(merchant, txn)
    if repeat_available:
        entry = merchant["processings"][str(txn.amount)]
        if len(entry["splits"]) == 1:
            cat_id = entry["splits"][0]["category_id"]
            cat_name = _category_name(ynab_categories, cat_id) or "?"
            preview = f"{cat_name} {amount_str}"
        else:
            preview = f"split into {len(entry['splits'])} categories"
        print()
        print(f"  {_bold('[Enter]')} to repeat last: {preview}")

    print(f"  Or:")
    print(f"    {_dim('s)')} Split into multiple categories")
    print(f"    {_dim('c)')} Pick a category")
    print(f"    {_dim('q)')} Quit categorizing — auto-flush remaining and finish")
    if unflushed_count:
        print(f"    {_dim('f)')} Flush {unflushed_count} pending to YNAB")
    print()

    while True:
        raw = input(_cyan("  > ")).strip().lower()
        if raw == "" and repeat_available:
            _apply_repeat(merchant, txn)
            return "categorized"
        if raw == "f" and unflushed_count:
            return "flush"
        if raw == "q":
            return "quit"
        if raw == "c":
            cat_id = _pick_category(merchant, ynab_categories, category_groups, ynab_client, budget_id)
            if cat_id is None:
                # User backed out of the picker; re-show prompt.
                continue
            txn.category_id = cat_id
            txn.subtransactions = []
            txn.memo = _prompt_memo(getattr(txn, "memo", "") or "")
            return "categorized"
        if raw == "s":
            subs = _collect_splits(txn, merchant, ynab_categories, category_groups, ynab_client, budget_id)
            if subs is None:
                continue
            txn.subtransactions = [
                {
                    "category_id": s["category_id"],
                    "amount": s["amount_milliunits"],
                    "memo": s["memo"],
                }
                for s in subs
            ]
            txn.category_id = None
            txn.memo = _prompt_memo(getattr(txn, "memo", "") or "")
            return "categorized"
        print(f"  Unrecognized: {raw!r}")


def _sort_key(store: ConfigStore):
    """Sort candidates by (merchant_alias, date_asc). Unknown-merchant
    transactions sort to the end."""
    def key(txn):
        mid = getattr(txn, "merchant_id", None)
        merchant = store.merchant_by_finwise_id(mid) if mid else None
        alias = merchant["alias"].lower() if merchant else "￿"
        d = getattr(txn, "date", None)
        return (alias, d if d is not None else date.max)
    return key


def _confirm(prompt: str) -> bool:
    """Yes/no prompt. Default Yes (empty -> True)."""
    raw = input(prompt).strip().lower()
    return raw in ("", "y", "yes")


def sync_transactions(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
    tx_store: Optional["TransactionsStore"] = None,
) -> None:
    """Phase 3 orchestrator. Fetches all transactions, dedupes, and walks
    the user through categorizing each non-auto-handled transaction.
    Pushes via the _PendingQueue (manual 'f' flush, auto-flush at end,
    Ctrl+C asks before flushing).

    Dedup uses a local sync map in transactions.json (TransactionsStore),
    not YNAB's import_id field, so YNAB-side deletes can re-import cleanly
    on the next run.
    """
    print("\n--- Transaction Sync ---")

    if tx_store is None:
        tx_store = TransactionsStore()

    try:
        fw_txns = fw_client.get_transactions()
    except Exception as e:
        print(f"Failed to fetch FinWise transactions: {e}")
        return

    try:
        ynab_txns = ynab_client.get_transactions(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB transactions: {e}")
        return

    try:
        ynab_categories = ynab_client.get_categories(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB categories: {e}")
        ynab_categories = []

    try:
        category_groups = ynab_client.get_category_groups_with_categories(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB category groups: {e}")
        category_groups = []

    candidates = merge_and_filter_transactions(fw_txns, ynab_txns, store, tx_store)
    candidates.sort(key=_sort_key(store))
    total = len(candidates)
    print(f"Transactions to process: {_yellow(str(total))}")

    queue = _PendingQueue()
    try:
        idx = 0
        while idx < total:
            txn = candidates[idx]
            outcome = _process_one_transaction(
                txn, idx + 1, total, queue.count(),
                store, ynab_client, budget_id,
                ynab_categories, category_groups,
            )
            if outcome == "quit":
                print(f"\nSkipping remaining {total - idx} transactions.")
                break
            if outcome == "flush":
                queue.flush(ynab_client, budget_id)
                continue   # re-process the same transaction
            if outcome == "categorized":
                queue.add(txn)
                # Only update merchant memory when an actual categorization
                # decision was made — skipping transfers, no-merchant pushes,
                # and pre-current-month auto-pushes whose category is None.
                # Otherwise we'd insert a meaningless empty entry into the
                # processings dict.
                has_decision = (
                    getattr(txn, "category_id", None) is not None
                    or bool(getattr(txn, "subtransactions", None))
                )
                if has_decision:
                    merchant = store.merchant_by_finwise_id(getattr(txn, "merchant_id", None))
                    if merchant:
                        _update_merchant_memory(store, merchant, txn)
            idx += 1
        # Normal end-of-loop: auto-flush whatever's pending.
        if queue.count() > 0:
            queue.flush(ynab_client, budget_id)
    except KeyboardInterrupt:
        # User explicitly chose; respect their answer. Don't fall through to
        # an unconditional finally that would re-flush.
        if queue.count() > 0:
            if _confirm(f"\nFlush {queue.count()} pending transactions before exit? [Y/n]: "):
                queue.flush(ynab_client, budget_id)
        raise
