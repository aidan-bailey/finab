"""Pure helpers for phase 3 transaction processing.

These functions and constants were extracted from finab.transactions
to give phase 3 a non-interactive surface. transactions.py re-exports
them so existing call sites keep working.

No interactive I/O (no input(), no network). Diagnostic print() calls
inside merge_and_filter_transactions are retained verbatim from the
original — they emit dedup diagnostics (counts and warnings) and aren't
worth refactoring out at this stage of the migration.
"""
from datetime import date
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore


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


# ANSI helpers stay in finab.transactions (they depend on sys.stdout.isatty).
# Inside the engine we accept colour loss in dedup diagnostics rather than
# import a presentation helper. The dedup print() lines stay as-is and use
# these no-ops instead.
def _yellow(s: str) -> str: return s
def _dim(s: str) -> str: return s


def merge_and_filter_transactions(
    fw_transactions,
    ynab_transactions,
    store: "ConfigStore",
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
    import uuid

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


def _update_merchant_memory(store: "ConfigStore", merchant: dict, txn) -> None:
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


def _sort_key(store: "ConfigStore"):
    """Sort candidates by (merchant_alias, date_asc). Unknown-merchant
    transactions sort to the end."""
    def key(txn):
        mid = getattr(txn, "merchant_id", None)
        merchant = store.merchant_by_finwise_id(mid) if mid else None
        alias = merchant["alias"].lower() if merchant else "￿"
        d = getattr(txn, "date", None)
        return (alias, d if d is not None else date.max)
    return key
