"""Pure helpers for phase 3 transaction processing.

These functions and constants were extracted from finab.transactions
to give phase 3 a non-interactive surface. transactions.py re-exports
them so existing call sites keep working.

No interactive I/O (no input(), no network). Diagnostic print() calls
inside merge_and_filter_transactions are retained verbatim from the
original — they emit dedup diagnostics (counts and warnings) and aren't
worth refactoring out at this stage of the migration.
"""
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any, Literal, Optional

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


@dataclass
class TransferMatch:
    """One detected transfer pair. keep_txn (the outflow) is pushed to YNAB
    with payee = dest_transfer_payee_id; YNAB auto-creates the mirror in the
    destination account, so suppress_txn (the inflow) is not pushed."""
    keep_txn: Any
    suppress_txn: Any
    dest_transfer_payee_id: str
    dest_alias: str
    confidence: str  # "high" | "low"


def _dest_transfer_payee(store, ynab_account_id):
    """(transfer_payee_id, alias) for a YNAB account id, or (None, None)."""
    if not ynab_account_id:
        return None, None
    acc = store.account_by_ynab_id(ynab_account_id)
    if not acc:
        return None, None
    ynab = acc.get("ynab") or {}
    return ynab.get("transfer_payee_id"), acc.get("alias")


def match_transfer_pairs(txns, store, *, window_days=1):
    """Pair equal-and-opposite cross-account transactions into transfers.

    Runs on the post-dedup batch (account_id already remapped to the YNAB
    account id). Pools CREATES ONLY (no ynab_id) so we never suppress a txn
    already on YNAB. Each transaction is used in at most one pair. Returns a
    list of TransferMatch.

    Confidence: HIGH iff there was exactly one viable partner AND (same day OR
    the two sides share a merchant_id); LOW otherwise.
    """
    pool = [t for t in txns
            if not getattr(t, "ynab_id", None) and getattr(t, "amount", 0)]
    by_amount: dict = {}
    for t in pool:
        if t.amount > 0:
            by_amount.setdefault(t.amount, []).append(t)
    outflows = sorted(
        [t for t in pool if t.amount < 0],
        key=lambda t: (t.date, str(t.import_id)),
    )

    used: set = set()
    matches: list = []
    for out in outflows:
        viable = []  # (inflow, dest_tp, dest_alias)
        for cand in by_amount.get(-out.amount, []):
            if id(cand) in used or cand.account_id == out.account_id:
                continue
            if abs((cand.date - out.date).days) > window_days:
                continue
            dest_tp, dest_alias = _dest_transfer_payee(store, cand.account_id)
            if dest_tp:
                viable.append((cand, dest_tp, dest_alias))
        if not viable:
            continue

        def _rank(item):
            cand = item[0]
            shared = bool(out.merchant_id and out.merchant_id == cand.merchant_id)
            return (0 if shared else 1, abs((cand.date - out.date).days), str(cand.import_id))

        viable.sort(key=_rank)
        best, dest_tp, dest_alias = viable[0]
        used.add(id(best))
        shared_merchant = bool(out.merchant_id and out.merchant_id == best.merchant_id)
        same_day = best.date == out.date
        confidence = "high" if (len(viable) == 1 and (same_day or shared_merchant)) else "low"
        matches.append(TransferMatch(
            keep_txn=out, suppress_txn=best,
            dest_transfer_payee_id=dest_tp, dest_alias=dest_alias or "?",
            confidence=confidence,
        ))
    return matches


CandidateStatus = Literal["pending", "auto", "decided", "flushed", "merged"]
"""
pending  — needs user input
auto     — engine auto-applied (inflow/transfer/transfer-pair)
decided  — user applied a category/split/transfer (incl. confirmed suggestion)
flushed  — pushed to YNAB
merged   — suppressed counterpart of a matched transfer; never pushed
"""


AutoReason = Literal[
    "inflow", "transfer", "no-merchant", "pre-month",
    "transfer-pair", "transfer-suggested", "transfer-merged",
]


@dataclass
class Candidate:
    """One transaction in the per-run workflow.

    `txn` is the FinWise-side Transaction; `merge_and_filter_transactions`
    may have already mutated its `import_id` to our durable id, and may
    have set `ynab_id` if this is an UPDATE rather than a CREATE.

    `warnings` holds human-readable strings the UI should surface non-
    destructively (e.g. via a ⚠ glyph) but that don't block flushing.
    """
    id: str                              # stable; we use txn.import_id (durable our_id)
    txn: Any                             # finab.models.Transaction — Any to avoid import cycle here
    status: CandidateStatus = "pending"
    auto_reason: Optional[AutoReason] = None
    # Snapshot of {category_id, subtransactions, payee_id, payee_name, memo}
    # taken at the moment a user decision is applied. Used by undo() to
    # restore the pre-decision state. None on pending or auto candidates.
    prior_state: Optional[dict] = None
    warnings: list = field(default_factory=list)
    transfer_partner_id: Optional[str] = None
    transfer_role: Optional[Literal["keep", "suppress"]] = None
    transfer_dest_alias: Optional[str] = None


class SyncEngine:
    """State machine for phase 3 (transaction sync).

    Construction does the work of `merge_and_filter_transactions` + the
    sort + the auto-rule pass from the original `_process_one_transaction`.
    The result is `self.candidates`, a list of Candidate objects in the
    order the UI should walk them.

    The engine never prints, never reads stdin, and never calls a network
    client during construction — flush() is the only method that calls
    YNAB.
    """

    def __init__(
        self,
        *,
        fw_transactions,
        ynab_transactions,
        ynab_categories,
        store,
        tx_store,
        transfer_match_window_days: int = 1,
    ):
        self._store = store
        self._tx_store = tx_store
        self._ynab_categories = ynab_categories

        # 1. Dedup and sort.
        merged = merge_and_filter_transactions(
            fw_transactions, ynab_transactions, store, tx_store
        )
        merged.sort(key=_sort_key(store))

        # 2. Transfer pre-pass: claim equal-and-opposite cross-account pairs
        # BEFORE per-candidate auto-rules (so the inflow side isn't booked
        # as income by rule (a)).
        self._match_by_id: dict = {}
        for mt in match_transfer_pairs(
            merged, store, window_days=transfer_match_window_days
        ):
            self._match_by_id[mt.keep_txn.import_id] = ("keep", mt)
            self._match_by_id[mt.suppress_txn.import_id] = ("suppress", mt)

        # 3. Build Candidate per txn and apply auto-rules (or transfer match).
        self.candidates: list[Candidate] = [
            self._build_candidate(txn) for txn in merged
        ]

    def _build_candidate(self, txn) -> Candidate:
        """Construct a Candidate around `txn`. Matched transfers are handled
        first; otherwise the normal auto-rules apply."""
        candidate = Candidate(id=txn.import_id, txn=txn)
        entry = self._match_by_id.get(txn.import_id)
        if entry is not None:
            self._apply_transfer_match(candidate, *entry)
            return candidate
        self._apply_auto_rules(candidate)
        return candidate

    def _apply_transfer_match(self, candidate: "Candidate", role: str, mt) -> None:
        """Configure a candidate that is one side of a matched transfer."""
        if role == "keep":
            candidate.txn.payee_id = mt.dest_transfer_payee_id
            candidate.txn.payee_name = None
            candidate.txn.category_id = None
            candidate.txn.subtransactions = []
            candidate.status = "auto" if mt.confidence == "high" else "pending"
            candidate.auto_reason = (
                "transfer-pair" if mt.confidence == "high" else "transfer-suggested"
            )
            candidate.transfer_role = "keep"
            candidate.transfer_partner_id = mt.suppress_txn.import_id
            candidate.transfer_dest_alias = mt.dest_alias
        else:  # suppress
            candidate.status = "merged"
            candidate.auto_reason = "transfer-merged"
            candidate.transfer_role = "suppress"
            candidate.transfer_partner_id = mt.keep_txn.import_id
            candidate.transfer_dest_alias = mt.dest_alias

    def _apply_auto_rules(self, candidate: "Candidate") -> None:
        """Apply inflow/transfer/no-merchant/pre-month/pending rules to an
        existing candidate, mutating its txn + status + auto_reason in place.
        (Extracted verbatim from the original _build_candidate body.)"""
        txn = candidate.txn

        # (a) Inflow
        if _is_inflow(txn):
            inflow_id = _find_inflow_category(self._ynab_categories)
            if inflow_id:
                txn.category_id = inflow_id
                txn.subtransactions = []
                candidate.status = "auto"
                candidate.auto_reason = "inflow"
                return

        merchant = None
        fw_mid = getattr(txn, "merchant_id", None)
        if fw_mid:
            merchant = self._store.merchant_by_finwise_id(fw_mid)

        # (b) Transfer (merchant linked to an account transfer payee)
        if _is_transfer(merchant):
            txn.payee_id = merchant["ynab"]["id"]
            txn.payee_name = None
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "auto"
            candidate.auto_reason = "transfer"
            return

        # (b2) Warning: FW says transfer but merchant isn't a transfer payee.
        if getattr(txn, "is_transfer", False):
            if merchant:
                candidate.warnings.append(
                    f"FinWise marks this as a transfer but merchant "
                    f"'{merchant.get('alias', '?')}' isn't linked to a YNAB "
                    f"account. Re-link via the Merchants screen."
                )
            else:
                candidate.warnings.append(
                    "FinWise marks this as a transfer but no merchant is "
                    "linked. It will push without a transfer payee."
                )

        # (c) No merchant
        if not merchant:
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "pending"
            candidate.auto_reason = "no-merchant"
            return

        # (d) Before current month
        if _is_before_current_month(txn):
            txn.payee_id = merchant["ynab"].get("id")
            txn.payee_name = None
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "pending"
            candidate.auto_reason = "pre-month"
            return

        # Default: pending, payee set from merchant.
        txn.payee_id = merchant["ynab"].get("id")
        txn.payee_name = None

    def _candidate(self, candidate_id: str) -> "Candidate":
        """Look up a candidate by id. Raises KeyError if not found."""
        for c in self.candidates:
            if c.id == candidate_id:
                return c
        raise KeyError(f"unknown candidate id: {candidate_id!r}")

    def _snapshot(self, txn) -> dict:
        """Capture the fields apply_*/undo cares about."""
        return {
            "category_id": getattr(txn, "category_id", None),
            "subtransactions": list(getattr(txn, "subtransactions", []) or []),
            "payee_id": getattr(txn, "payee_id", None),
            "payee_name": getattr(txn, "payee_name", None),
            "memo": getattr(txn, "memo", None),
        }

    def apply_category(
        self,
        candidate_id: str,
        *,
        category_id: str,
        memo: Optional[str] = None,
    ) -> None:
        """Record a single-category decision for the named candidate.

        Mutates the Transaction (category_id, subtransactions=[], optional memo),
        updates merchant memory (categories_used + processings) if the candidate
        has a resolvable merchant, and sets status='decided'.

        Memory update is by-design last-write-wins per (merchant, amount).
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        c.txn.category_id = category_id
        c.txn.subtransactions = []
        if memo is not None:
            c.txn.memo = memo
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"

    def apply_split(
        self,
        candidate_id: str,
        *,
        splits: list,
        memo: Optional[str] = None,
    ) -> None:
        """Record a multi-category split for the named candidate.

        `splits` is a list of {category_id, amount, memo} dicts. The sum
        of `amount` values must equal txn.amount; this is the same
        invariant the YNAB API enforces server-side, and surfacing it
        here lets the UI catch mistakes before flush.
        """
        c = self._candidate(candidate_id)
        total = sum(s["amount"] for s in splits)
        if total != c.txn.amount:
            raise ValueError(
                f"split amounts must sum to txn.amount "
                f"({c.txn.amount}); got {total}"
            )
        c.prior_state = self._snapshot(c.txn)
        c.txn.category_id = None
        c.txn.subtransactions = [
            {"category_id": s["category_id"], "amount": s["amount"], "memo": s.get("memo", "") or ""}
            for s in splits
        ]
        if memo is not None:
            c.txn.memo = memo
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"

    def apply_transfer(
        self,
        candidate_id: str,
        *,
        transfer_payee_id: str,
    ) -> None:
        """Force-mark the candidate as a transfer to one of the user's
        own accounts. Used when the auto-rule didn't fire because the
        merchant wasn't linked to an account.

        Does NOT update merchant memory — transfers aren't categorizations.
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        c.txn.payee_id = transfer_payee_id
        c.txn.payee_name = None
        c.txn.category_id = None
        c.txn.subtransactions = []
        c.status = "decided"

    def apply_history(
        self,
        candidate_id: str,
        *,
        entry: dict,
    ) -> None:
        """Record a repeat-from-history decision for the named candidate.

        `entry` is a `processings` entry: {parent_memo, splits} where
        splits is a list of {category_id, amount_milliunits, memo}.

        Multi-split entries are scaled proportionally to the current
        txn.amount (mirroring _apply_processing_to_txn from the engine
        helpers). Snapshots prior state so undo works the same as for
        apply_category / apply_split.

        Updates merchant memory (re-applying an entry counts as a
        categorization for that amount).
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        _apply_processing_to_txn(entry, c.txn)
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"

    def undo(self, candidate_id: str) -> None:
        """Revert a user decision: status decided -> pending, restore
        snapshotted fields on txn. Does NOT revert merchant memory
        (last-write-wins by amount; an undo+re-decide just overwrites).
        """
        c = self._candidate(candidate_id)
        if c.status != "decided":
            raise ValueError(
                f"cannot undo candidate with status {c.status!r}; "
                f"only 'decided' supports undo"
            )
        if c.prior_state is None:
            raise ValueError(
                f"no prior_state recorded for candidate {candidate_id!r}"
            )
        snap = c.prior_state
        c.txn.category_id = snap["category_id"]
        c.txn.subtransactions = snap["subtransactions"]
        c.txn.payee_id = snap["payee_id"]
        c.txn.payee_name = snap["payee_name"]
        c.txn.memo = snap["memo"]
        c.prior_state = None
        c.status = "pending"

    def flush(self, ynab_client, budget_id: str) -> None:
        """Push all decided + auto candidates to YNAB.

        Two batches: creates (no ynab_id) and updates (has ynab_id).
        Each batch's candidates are marked 'flushed' only after that
        batch's API call returns success — a partial failure (creates
        OK then updates raise) leaves the pre-failure batch flushed
        and the failing batch still 'decided' for retry.

        Raises on API failure (no swallowing).
        """
        # Snapshot which candidates we're attempting in this flush.
        pushable = [
            c for c in self.candidates
            if c.status in ("decided", "auto")
        ]
        creates = [c for c in pushable if not getattr(c.txn, "ynab_id", None)]
        updates = [c for c in pushable if getattr(c.txn, "ynab_id", None)]

        if creates:
            ynab_client.create_transactions(budget_id, [c.txn for c in creates])
            for c in creates:
                c.status = "flushed"
        if updates:
            ynab_client.update_transactions(budget_id, [c.txn for c in updates])
            for c in updates:
                c.status = "flushed"
