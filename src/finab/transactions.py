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


# Names YNAB might use for the inflow category. Checked in this order.
_INFLOW_CATEGORY_NAMES = (
    "inflow: ready to assign",
    "ready to assign",
    "inflow: to be budgeted",
    "to be budgeted",
)


# Account types that are off-budget in YNAB ("Tracking" accounts). Transactions
# on these accounts legitimately have no category — YNAB doesn't budget them.
# Used to suppress the "uncategorized -> update" path in dedup so the user
# isn't re-prompted to categorize tracking-account transactions every sync.
_TRACKING_ACCOUNT_TYPES = frozenset({
    "otherAsset", "otherLiability",
    "mortgage", "autoLoan", "studentLoan",
    "personalLoan", "medicalDebt", "otherDebt",
})


def _account_is_tracking(acc: dict) -> bool:
    """True if the stored account's YNAB type is a tracking (off-budget) type."""
    if not acc:
        return False
    return (acc.get("ynab", {}) or {}).get("type") in _TRACKING_ACCOUNT_TYPES


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

    # Diagnostic: confirm overlap between our stored mappings and YNAB-side
    # import_ids. If this overlap is 0 we know dedup is dead in the water and
    # every FW txn will go through new/rotate (re-prompting the user).
    stored_our_ids = set(tx_store._data.get("synced_transactions", {}).values())
    live_import_ids = set(ynab_by_import_id.keys())
    overlap = stored_our_ids & live_import_ids
    print(
        f"  [dedup] YNAB: {len(ynab_transactions)} total, "
        f"{len(live_import_ids)} have import_id | "
        f"stored: {len(stored_our_ids)} | "
        f"overlap (stored ∩ live): {len(overlap)}"
    )
    if stored_our_ids and not overlap:
        # Show samples so we can spot format/case mismatches at a glance.
        s_sample = sorted(stored_our_ids)[:3]
        l_sample = sorted(live_import_ids)[:3]
        print(_yellow(
            f"  [dedup] ⚠ No overlap! Sample stored: {s_sample}, "
            f"sample YNAB: {l_sample}"
        ))

    # Prune any stored import_ids no longer present in YNAB.
    tx_store.prune_stale(set(ynab_by_import_id.keys()))

    counts = {
        "skip_no_account": 0, "skip_ignored": 0,
        "skip_categorized": 0,
        "update": 0, "rotate": 0, "new": 0,
    }

    out = []
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            counts["skip_no_account"] += 1
            continue
        if acc.get("ignore_transactions"):
            counts["skip_ignored"] += 1
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            counts["skip_no_account"] += 1
            continue

        fw_uuid = fw_txn.import_id  # FW's own UUID, set by from_finwise
        our_id = tx_store.import_id_for(fw_uuid) if fw_uuid else None

        if our_id and our_id in ynab_by_import_id:
            # Already synced and YNAB still has it.
            ynab_match = ynab_by_import_id[our_id]
            has_category = getattr(ynab_match, "category_id", None)
            # Split transactions store the category on each subtransaction,
            # not on the parent (parent's category_id is None). Treat any
            # non-deleted subtransaction as evidence of categorization.
            subs = getattr(ynab_match, "subtransactions", None) or []
            has_splits = any(not getattr(s, "deleted", False) for s in subs)
            # Account-to-account transfers in YNAB legitimately have no
            # category — the transfer_account_id field IS the resolution.
            # Without this check we'd re-prompt the user for every transfer
            # on every sync.
            is_transfer = getattr(ynab_match, "transfer_account_id", None)
            # Tracking accounts (off-budget) never have categories. Their
            # YNAB twins always have category_id=None; that's not a sign of
            # needing categorization.
            is_tracking = _account_is_tracking(acc)
            if has_category or has_splits or is_transfer or is_tracking:
                # Already resolved — skip.
                counts["skip_categorized"] += 1
                continue
            fw_txn.ynab_id = str(ynab_match.id)
            fw_txn.import_id = our_id  # keep stable for update push
            fw_txn.category_id = None
            counts["update"] += 1
            print(_dim(
                f"  [dedup] update: fw={fw_uuid[:8]} "
                f"{getattr(fw_txn, 'memo', '?')[:40]!r} "
                f"(YNAB twin uncategorized)"
            ))
        else:
            # Either never synced, or YNAB-twin missing (user deleted).
            # Rotate: fresh UUID, overwrite stored, push as new.
            reason = "rotate" if our_id else "new"
            counts[reason] += 1
            new_id = uuid.uuid4().hex
            tx_store.record(fw_uuid, new_id)
            fw_txn.import_id = new_id
            if reason == "rotate":
                print(_dim(
                    f"  [dedup] rotate: fw={fw_uuid[:8]} "
                    f"{getattr(fw_txn, 'memo', '?')[:40]!r} "
                    f"(prior our_id not in YNAB)"
                ))

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)

    print(
        f"  [dedup] skipped: {counts['skip_no_account']} no-account, "
        f"{counts['skip_ignored']} ignored, "
        f"{counts['skip_categorized']} already-categorized | "
        f"queued: {counts['new']} new, {counts['update']} update, "
        f"{counts['rotate']} rotate"
    )
    return out


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


def _closest_processing(merchant: dict, txn):
    """Return (amount_key, entry) for the processing entry whose amount is
    closest to txn.amount by absolute difference. Ties are broken by
    insertion order (first wins). Returns None if there are no
    processings or txn has no amount."""
    if not merchant:
        return None
    processings = merchant.get("processings") or {}
    if not processings:
        return None
    amt = getattr(txn, "amount", None)
    if amt is None:
        return None

    best = None
    best_diff = None
    for k, entry in processings.items():
        try:
            k_amt = int(k)
        except (TypeError, ValueError):
            continue
        diff = abs(k_amt - amt)
        if best_diff is None or diff < best_diff:
            best = (k, entry)
            best_diff = diff
    return best


def _apply_repeat(merchant: dict, txn) -> None:
    """Replay the closest-amount processing entry onto txn. Delegates to
    _apply_processing_to_txn so multi-split entries scale to txn.amount.
    Memos use fresh defaults; parent memo stays as the FinWise description."""
    chosen = _closest_processing(merchant, txn)
    if chosen is None:
        return
    _, entry = chosen
    _apply_processing_to_txn(entry, txn)


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
    # c.id from the YNAB SDK is a uuid.UUID instance; stored ids are str.
    # Compare on str(c.id) so direct-equality lookups work regardless.
    target = str(category_id) if category_id is not None else None
    if target is None:
        return None
    for c in categories:
        if str(c.id) == target:
            return c.name
    return None


def _render_splits(entry: dict, ynab_categories: list, scale: float = 1.0) -> str:
    """Render a processing entry's splits as a human-readable summary.

    Single split: just the category name.
    Multi-split: comma-separated 'Category <amount>' pairs, optionally
    scaled by `scale` (e.g. 2.0 when the current transaction is twice the
    stored amount). Amounts shown in standard currency units, signed.
    """
    splits = entry.get("splits", []) or []
    if len(splits) == 1:
        return _category_name(ynab_categories, splits[0]["category_id"]) or "?"
    parts = []
    for s in splits:
        cn = _category_name(ynab_categories, s["category_id"]) or "?"
        amt = int(round(s["amount_milliunits"] * scale))
        parts.append(f"{cn} {amt/1000:.2f}")
    return ", ".join(parts)


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


def _apply_processing_to_txn(entry: dict, txn) -> None:
    """Apply a chosen processing entry to txn.
    - Single split: set category_id, clear subtransactions.
    - Multi-split: scale split amounts proportionally to txn.amount so the
      sum matches the current transaction's total exactly.
    """
    splits = entry.get("splits", []) or []
    if len(splits) == 1:
        txn.category_id = splits[0]["category_id"]
        txn.subtransactions = []
        return
    # Multi-split: scale proportionally to current txn.amount.
    total_prior = sum(s["amount_milliunits"] for s in splits)
    if total_prior == 0:
        # Degenerate — fall back to applying just the first split's category.
        txn.category_id = splits[0]["category_id"]
        txn.subtransactions = []
        return
    ratio = txn.amount / total_prior
    scaled = [
        {
            "category_id": s["category_id"],
            "amount": int(round(s["amount_milliunits"] * ratio)),
            "memo": "",
        }
        for s in splits
    ]
    # Absorb rounding error into the last split so totals always reconcile.
    diff = txn.amount - sum(x["amount"] for x in scaled)
    if scaled and diff:
        scaled[-1]["amount"] += diff
    txn.category_id = None
    txn.subtransactions = scaled


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
