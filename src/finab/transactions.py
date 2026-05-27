"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
import json
import os
import sys
from pathlib import Path
from typing import Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore

# --- Re-exports from finab.engine.sync ---
# These functions and constants moved to finab.engine.sync; we re-export
# them here so existing import call sites (and the test suite) keep working.
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
)


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


# --- Color helpers (mirror main.py; kept local to avoid cross-module imports). ---
def _color(code: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"

def _bold(s: str) -> str:   return _color("1", s)
def _dim(s: str) -> str:    return _color("2", s)
def _green(s: str) -> str:  return _color("32", s)
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
            new_grp = ynab_client.create_category_group(budget_id, grp_name)
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

    new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)

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

        Each list is cleared immediately after its own API call succeeds,
        so a partial failure (creates OK then updates raise) doesn't leave
        the already-pushed creates in the queue to be re-posted on retry.
        YNAB would dedup re-posts via import_id, but explicit clearing is
        clearer than relying on that contract.
        """
        creates_snap = list(self.creates)
        updates_snap = list(self.updates)
        if creates_snap:
            print(f"  Pushing {len(creates_snap)} new transaction(s) to YNAB...")
            ynab_client.create_transactions(budget_id, creates_snap)
            self.creates.clear()
            print(f"  → Created {len(creates_snap)} transaction(s)")
        if updates_snap:
            print(f"  Pushing {len(updates_snap)} updated transaction(s) to YNAB...")
            ynab_client.update_transactions(budget_id, updates_snap)
            self.updates.clear()
            print(f"  → Updated {len(updates_snap)} transaction(s)")
        return True


def _pick_from_processings(merchant: dict, txn, ynab_categories: list) -> bool:
    """Show merchant.processings as a numbered list of prior categorizations
    (each entry's amount + categories used). User picks one to apply to the
    current transaction.

    Returns True if a selection was applied (caller treats as categorized);
    False if user backed out. Mutates txn directly on success.
    """
    processings = merchant.get("processings") or {}
    if not processings:
        print(_dim("  (no prior processings for this merchant)"))
        return False

    entries = list(processings.items())  # insertion order
    closest = _closest_processing(merchant, txn)
    closest_key = closest[0] if closest else None

    print()
    print(f"  {_bold('Prior categorizations for')} '{merchant.get('alias', '?')}':")
    for i, (amt_key, entry) in enumerate(entries, start=1):
        try:
            amt = int(amt_key) / 1000.0
            amt_str = f"{amt:>10.2f}"
        except (TypeError, ValueError):
            amt_str = f"{amt_key:>10}"
        splits = entry.get("splits", []) or []
        rendered = _render_splits(entry, ynab_categories)
        if len(splits) == 1:
            line = f"{amt_str}   {rendered}"
        else:
            line = f"{amt_str}   split: {rendered}"
        marker = f" {_dim('(closest)')}" if amt_key == closest_key else ""
        print(f"   {i:>3}. {line}{marker}")
    print()

    while True:
        raw = input(_cyan("  Pick a number, Enter to go back: ")).strip()
        if not raw:
            return False
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(entries):
                _, entry = entries[n - 1]
                _apply_processing_to_txn(entry, txn)
                return True
            print(f"  Out of range (1..{len(entries)})")
            continue
        print(f"  Unrecognized: {raw!r}")


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
    amount_str = f"{txn.amount / 1000:.2f}"

    # --- (a) Positive amount: auto-inflow ---
    if _is_inflow(txn):
        inflow_id = _find_inflow_category(ynab_categories)
        if inflow_id:
            txn.category_id = inflow_id
            txn.subtransactions = []
            print(f"  {_dim(f'[{idx}/{total}]')} {_green('auto-inflow')} {amount_str} -> Ready to Assign")
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
        print(f"  {_dim(f'[{idx}/{total}]')} {_cyan('auto-transfer')} {amount_str} -> {merchant.get('alias', '?')}")
        return "categorized"

    # --- (c2) Sanity check: FinWise says transfer but our merchant link
    # doesn't reflect that. Print a loud warning so the user can fix the
    # merchant linkage; otherwise the transaction will go to YNAB with no
    # transfer-payee and YNAB won't pair it with the matching leg.
    if getattr(txn, "is_transfer", False):
        merchant_alias = merchant.get("alias") if merchant else None
        if merchant_alias:
            print(_yellow(
                f"  {_dim(f'[{idx}/{total}]')} ⚠ FinWise marks this as a "
                f"transfer but merchant '{merchant_alias}' isn't linked to "
                f"a YNAB account. Re-run Phase 2 (merchant sync) and set "
                f"the alias to a YNAB account name to fix."
            ))
        else:
            print(_yellow(
                f"  {_dim(f'[{idx}/{total}]')} ⚠ FinWise marks this as a "
                f"transfer but no merchant is linked. The transaction will "
                f"be pushed without a transfer payee."
            ))

    # --- (d) No merchant: push uncategorized ---
    if not merchant:
        txn.category_id = None
        txn.subtransactions = []
        memo = getattr(txn, "memo", "") or "(no memo)"
        print(f"  {_dim(f'[{idx}/{total}]')} {_yellow('no-merchant')} {amount_str} {_dim('-- pushed uncategorized:')} {memo}")
        return "categorized"

    # --- (d2) Pre-current-month: push with payee but no category prompt ---
    if _is_before_current_month(txn):
        txn.payee_id = merchant["ynab"].get("id")
        txn.payee_name = None
        txn.category_id = None
        txn.subtransactions = []
        print(f"  {_dim(f'[{idx}/{total}]')} {_dim('pre-month')} {amount_str} -> {merchant.get('alias', '?')} {_dim('(no category)')}")
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
    print(f"  {_dim('Amount:')}    {amount_str}")
    print(f"  {_dim('Memo:')}      {getattr(txn, 'memo', '') or _dim('(none)')}")

    closest = _closest_processing(merchant, txn)
    repeat_available = closest is not None
    if repeat_available:
        closest_key, closest_entry = closest
        try:
            prior_amt = int(closest_key)
        except (TypeError, ValueError):
            prior_amt = txn.amount
        prior_amt_str = f"{prior_amt/1000:.2f}"
        splits = closest_entry.get("splits", []) or []
        is_exact = prior_amt == txn.amount
        if len(splits) == 1:
            rendered = _render_splits(closest_entry, ynab_categories)
            if is_exact:
                preview = f"{rendered}  {_dim(f'(last: {prior_amt_str})')}"
            else:
                preview = (
                    f"{rendered}  "
                    f"{_dim(f'(last used at {prior_amt_str})')}"
                )
        else:
            if is_exact:
                rendered = _render_splits(closest_entry, ynab_categories)
                preview = f"split: {rendered}"
            else:
                # Multi-split with different amount: scale to current txn amount.
                total_prior = sum(
                    s["amount_milliunits"] for s in splits
                ) or prior_amt or 1
                scale = txn.amount / total_prior
                rendered = _render_splits(
                    closest_entry, ynab_categories, scale=scale
                )
                preview = (
                    f"split {_dim(f'(scaled from {prior_amt_str})')}: "
                    f"{rendered}"
                )
        print()
        print(f"  {_bold('[Enter]')} to repeat: {preview}")

    print(f"  Or:")
    print(f"    {_dim('s)')} Split into multiple categories")
    print(f"    {_dim('c)')} Pick a category")
    if merchant.get("processings"):
        print(f"    {_dim('r)')} Repeat from history")
    print(f"    {_dim('q)')} Quit categorizing — auto-flush remaining and finish")
    if unflushed_count:
        print(f"    {_dim('f)')} Flush {unflushed_count} pending to YNAB")
    print()

    while True:
        raw = input(_cyan("  > ")).strip().lower()
        if raw == "" and repeat_available:
            _apply_repeat(merchant, txn)
            print(f"  {_green('→ repeated')} last processing for this amount")
            return "categorized"
        if raw == "f" and unflushed_count:
            return "flush"
        if raw == "q":
            return "quit"
        if raw == "r":
            if _pick_from_processings(merchant, txn, ynab_categories):
                print(f"  {_green('→ applied')} processing from history")
                return "categorized"
            continue
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

    print("Fetching FinWise transactions...")
    fw_txns = fw_client.get_transactions()
    print(f"  Fetched {_yellow(str(len(fw_txns)))} FinWise transactions")

    print("Fetching YNAB transactions...")
    ynab_txns = ynab_client.get_transactions(budget_id)
    print(f"  Fetched {_yellow(str(len(ynab_txns)))} YNAB transactions")

    print("Fetching YNAB categories...")
    ynab_categories = ynab_client.get_categories(budget_id)
    print(f"  Fetched {_yellow(str(len(ynab_categories)))} YNAB categories")

    print("Fetching YNAB category groups...")
    category_groups = ynab_client.get_category_groups_with_categories(budget_id)
    print(f"  Fetched {_yellow(str(len(category_groups)))} YNAB category groups")

    candidates = merge_and_filter_transactions(fw_txns, ynab_txns, store, tx_store)
    candidates.sort(key=_sort_key(store))
    total = len(candidates)
    print(f"\nAfter dedup: {_yellow(str(total))} transactions to process "
          f"{_dim(f'(from {len(fw_txns)} FinWise)')}")

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
