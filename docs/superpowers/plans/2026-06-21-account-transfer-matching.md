# Account-Transfer Matching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-detect transfers between the user's own accounts by pairing an outflow with an equal-and-opposite inflow, push one side as a YNAB transfer, and suppress the counterpart — with a confidence model gating auto-apply vs suggest.

**Architecture:** A pure matching pre-pass (`match_transfer_pairs`) runs inside `SyncEngine.__init__` after dedup, before the per-candidate auto-rules, so the inflow side is claimed before the inflow→income rule can grab it. Matched pairs become a *keep* candidate (pushed as a transfer) and a *merged* candidate (suppressed, recorded against the keep side's import_id so dedup skips it forever). A prerequisite fix makes `FinWiseClient.get_transactions` paginate (it currently only ever sees 100 of 1067 transactions).

**Tech Stack:** Python 3.14, Pydantic v2, Textual TUI, pytest, `uv` package manager.

**Spec:** `docs/superpowers/specs/2026-06-21-account-transfer-matching-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/finab/client.py` | FinWise transport wrapper | Paginate `get_transactions`. |
| `src/finab/models.py` | Unified models | Add `Transaction.fw_uuid` (original FinWise id, survives import_id rotation). |
| `src/finab/engine/sync.py` | Phase-3 pure logic + state machine | `TransferMatch` + `match_transfer_pairs`; new `Candidate` fields, statuses, reasons; wire matching into `SyncEngine`; `confirm_transfer_match`; transfer-aware `undo`; suppression recording in `flush`. |
| `src/finab/transactions.py` | Re-export surface | Re-export `match_transfer_pairs`, `TransferMatch`. |
| `src/finab/config.py` | Top-level config helpers | `load_/save_transfer_match_window_days`. |
| `src/finab/tui/screens/sync.py` | Sync screen | `t` accepts a suggested pair; refresh merged rows. |
| `src/finab/tui/widgets/pending_list.py` | List row glyphs | Glyphs/classes for the 3 new states. |
| `src/finab/tui/widgets/transaction_card.py` | Detail card | Render merged/transfer states + destination. |
| `src/finab/tui/screens/settings.py` | Settings display | Show the window-days value. |
| `src/finab/tui/styles.tcss` | Styles | Colors for new glyph classes. |

**Test files:** `tests/test_client.py` (new), `tests/test_models.py` (may exist — check), `tests/engine/test_transfer_matching.py` (new), `tests/engine/test_sync_engine.py` (extend), `tests/tui/` (extend).

**Implementation refinement over spec:** matching pools **creates only** (txns with no `ynab_id`). Suppressing a txn already present in YNAB would leave it uncategorized while YNAB also auto-creates the mirror — a duplicate. Both sides being creates is the common case; stragglers fall to manual `t`.

---

### Task 1: FinWise pagination

**Files:**
- Modify: `src/finab/client.py:31-58`
- Test: `tests/test_client.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_client.py
"""Tests for FinWiseClient.get_transactions pagination."""
import json
from finab.client import FinWiseClient


class _FakeTransport:
    """Returns canned pages keyed by the requested pageNumber."""
    def __init__(self, pages):
        self._pages = pages          # {pageNumber: [raw_txn_dict, ...]}
        self.calls = []              # list of params dicts seen

    def get(self, path, *, params=None):
        self.calls.append(params)
        page = json.loads(params["pagination"])["pageNumber"]
        return self._pages.get(page, [])


def _raw(i):
    """Minimal raw FinWise transaction dict accepted by FinWiseTransaction."""
    return {
        "id": f"fw-{i}", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z", "description": f"txn {i}",
        "accountId": "acc-1", "amount": {"amount": 1, "currencyCode": "ZAR"},
        "date": "2026-01-01T00:00:00Z", "merchantId": "m-1",
        "userId": "u-1", "needsReview": False,
    }


def _client_with(pages):
    c = FinWiseClient.__new__(FinWiseClient)        # bypass __init__/network
    class _Inner: pass
    c._client = _Inner()
    c._client._transport = _FakeTransport(pages)
    return c


def test_get_transactions_paginates_until_short_page():
    # 500 on page 1 (full), 3 on page 2 (short → stop).
    pages = {1: [_raw(i) for i in range(500)], 2: [_raw(i) for i in range(500, 503)]}
    c = _client_with(pages)
    txns = c.get_transactions()
    assert len(txns) == 503
    # Two page requests, JSON-encoded pagination param.
    assert len(c._client._transport.calls) == 2
    assert json.loads(c._client._transport.calls[0]["pagination"]) == {"pageNumber": 1, "pageSize": 500}
    assert json.loads(c._client._transport.calls[1]["pagination"]) == {"pageNumber": 2, "pageSize": 500}


def test_get_transactions_single_short_page_stops_immediately():
    c = _client_with({1: [_raw(i) for i in range(10)]})
    txns = c.get_transactions()
    assert len(txns) == 10
    assert len(c._client._transport.calls) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_client.py -v`
Expected: FAIL — current `get_transactions` calls `_transport.get("/transactions")` with no params and doesn't loop.

- [ ] **Step 3: Implement pagination**

Replace the body of `get_transactions` in `src/finab/client.py` (keep the signature and the date-filter/conversion tail):

```python
    def get_transactions(
        self, start_date: Optional[date] = None, end_date: Optional[date] = None
    ) -> List[Transaction]:
        """
        Fetches ALL transactions from FinWise via pagination and optionally
        filters by date client-side.

        FinWise paginates with a JSON-encoded query param:
            /transactions?pagination={"pageNumber":N,"pageSize":M}
        The transport returns a bare list (headers discarded), so we stop
        when a page comes back shorter than the requested page size.
        """
        import json

        PAGE_SIZE = 500
        finwise_txns = []
        page = 1
        while True:
            batch = self._client._transport.get(
                "/transactions",
                params={"pagination": json.dumps({"pageNumber": page, "pageSize": PAGE_SIZE})},
            )
            if not isinstance(batch, list):
                raise ValueError(
                    f"Unexpected response format from FinWise API: {type(batch)}"
                )
            finwise_txns.extend(FinWiseTransaction.model_validate(t) for t in batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1

        if start_date:
            finwise_txns = [t for t in finwise_txns if t.date.date() >= start_date]
        if end_date:
            finwise_txns = [t for t in finwise_txns if t.date.date() <= end_date]

        return [Transaction.from_finwise(t) for t in finwise_txns]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_client.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/client.py tests/test_client.py
git commit -m "fix(client): paginate FinWise get_transactions (was capped at 100)"
```

---

### Task 2: `Transaction.fw_uuid` field

**Files:**
- Modify: `src/finab/models.py:132-166`
- Test: `tests/engine/test_sync_helpers.py` (add one test; file already imports models)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/engine/test_sync_helpers.py
from finab.models import FinWiseTransaction, Transaction


def test_from_finwise_sets_fw_uuid_to_source_id():
    raw = {
        "id": "fw-abc", "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z", "description": "x",
        "accountId": "acc-1", "amount": {"amount": 5, "currencyCode": "ZAR"},
        "date": "2026-01-01T00:00:00Z", "merchantId": "m-1",
        "userId": "u-1", "needsReview": False,
    }
    txn = Transaction.from_finwise(FinWiseTransaction.model_validate(raw))
    assert txn.fw_uuid == "fw-abc"
    assert txn.import_id == "fw-abc"   # unchanged: still seeds import_id too
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_sync_helpers.py::test_from_finwise_sets_fw_uuid_to_source_id -v`
Expected: FAIL — `Transaction` has no attribute `fw_uuid`.

- [ ] **Step 3: Add the field and set it in `from_finwise`**

In `src/finab/models.py`, add the field to `Transaction` (after `ynab_id`):

```python
    ynab_id: Optional[str] = None
    fw_uuid: Optional[str] = None  # original FinWise id; survives import_id rotation
```

In `Transaction.from_finwise`, add `fw_uuid=txn.id` alongside `import_id=txn.id`:

```python
            import_id=txn.id,
            fw_uuid=txn.id,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_sync_helpers.py::test_from_finwise_sets_fw_uuid_to_source_id -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/models.py tests/engine/test_sync_helpers.py
git commit -m "feat(models): add Transaction.fw_uuid (durable FinWise id)"
```

---

### Task 3: Candidate transfer fields + new status/reason literals

**Files:**
- Modify: `src/finab/engine/sync.py:404-435`
- Test: `tests/engine/test_sync_engine.py` (extend `TestCandidate`)

- [ ] **Step 1: Write the failing test**

```python
# add to TestCandidate in tests/engine/test_sync_engine.py
def test_candidate_has_transfer_fields_defaulting_none(self):
    c = Candidate(id="x", txn=object())
    assert c.transfer_partner_id is None
    assert c.transfer_role is None
    assert c.transfer_dest_alias is None

def test_candidate_accepts_merged_status_and_transfer_reason(self):
    c = Candidate(
        id="x", txn=object(), status="merged",
        auto_reason="transfer-merged", transfer_role="suppress",
        transfer_partner_id="y", transfer_dest_alias="Savings",
    )
    assert c.status == "merged"
    assert c.auto_reason == "transfer-merged"
    assert c.transfer_role == "suppress"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestCandidate -v`
Expected: FAIL — unexpected keyword argument `transfer_partner_id`.

- [ ] **Step 3: Extend the literals and the dataclass**

In `src/finab/engine/sync.py`, update the type aliases:

```python
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
```

Add fields to the `Candidate` dataclass (after `warnings`):

```python
    warnings: list = field(default_factory=list)
    transfer_partner_id: Optional[str] = None
    transfer_role: Optional[Literal["keep", "suppress"]] = None
    transfer_dest_alias: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestCandidate -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): add Candidate transfer fields + merged status"
```

---

### Task 4: `match_transfer_pairs` pure function

**Files:**
- Modify: `src/finab/engine/sync.py` (add after `_sort_key`, before `CandidateStatus`)
- Modify: `src/finab/transactions.py:19-35` (re-export)
- Test: `tests/engine/test_transfer_matching.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/engine/test_transfer_matching.py
"""Tests for match_transfer_pairs — the pure transfer-pairing pass."""
from datetime import date

from finab.engine.sync import match_transfer_pairs, TransferMatch
from finab.models import Transaction
from finab.store import ConfigStore


def _store(tmp_path):
    s = ConfigStore(tmp_path / "config.json")
    s.add_account(
        alias="Cheque",
        fw_record={"id": "fw-a", "name": "Cheque", "type": "checking", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-a", "name": "Cheque", "type": "checking", "balance": 0, "transfer_payee_id": "tp-a"},
    )
    s.add_account(
        alias="Savings",
        fw_record={"id": "fw-b", "name": "Savings", "type": "savings", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-b", "name": "Savings", "type": "savings", "balance": 0, "transfer_payee_id": "tp-b"},
    )
    return s


def _txn(uuid, amount, ynab_acc, *, day=10, merchant=None):
    return Transaction(
        import_id=uuid, fw_uuid=uuid, amount=amount,
        date=date(2026, 5, day), account_id=ynab_acc, merchant_id=merchant, memo="m",
    )


def test_same_day_exact_pair_is_high_confidence(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    matches = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)
    assert len(matches) == 1
    m = matches[0]
    assert isinstance(m, TransferMatch)
    assert m.keep_txn is out and m.suppress_txn is inn
    assert m.dest_transfer_payee_id == "tp-b"   # destination is the inflow's account
    assert m.dest_alias == "Savings"
    assert m.confidence == "high"


def test_next_day_same_merchant_is_high(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="shared")
    inn = _txn("i", 50000, "yn-b", day=11, merchant="shared")
    m = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)[0]
    assert m.confidence == "high"


def test_next_day_different_merchant_is_low(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="x")
    inn = _txn("i", 50000, "yn-b", day=11, merchant="y")
    m = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)[0]
    assert m.confidence == "low"


def test_multiple_candidates_is_low_and_prefers_shared_merchant(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="shared")
    inn1 = _txn("i1", 50000, "yn-b", day=10, merchant="other")
    inn2 = _txn("i2", 50000, "yn-b", day=10, merchant="shared")
    matches = match_transfer_pairs([out, inn1, inn2], _store(tmp_path), window_days=1)
    assert len(matches) == 1
    assert matches[0].suppress_txn is inn2     # shared merchant wins the tie
    assert matches[0].confidence == "low"      # >1 candidate → low


def test_same_account_is_not_matched(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-a", day=10)
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_outside_window_is_not_matched(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=14)
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_destination_without_transfer_payee_is_skipped(tmp_path):
    s = _store(tmp_path)
    # Blank out Savings' transfer payee.
    acc = s.account_by_ynab_id("yn-b")
    acc["ynab"]["transfer_payee_id"] = None
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    assert match_transfer_pairs([out, inn], s, window_days=1) == []


def test_already_in_ynab_is_excluded(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    inn.ynab_id = "already-there"     # an update, not a create
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_one_to_one_consumption(tmp_path):
    out1 = _txn("o1", -50000, "yn-a", day=10)
    out2 = _txn("o2", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    matches = match_transfer_pairs([out1, out2, inn], _store(tmp_path), window_days=1)
    assert len(matches) == 1   # only one outflow can claim the single inflow
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_transfer_matching.py -v`
Expected: FAIL — `cannot import name 'match_transfer_pairs'`.

- [ ] **Step 3: Implement `TransferMatch` + `match_transfer_pairs`**

In `src/finab/engine/sync.py`, add after `_sort_key` (around line 401):

```python
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
```

- [ ] **Step 4: Re-export from `transactions.py`**

In `src/finab/transactions.py`, add to the import block (after `merge_and_filter_transactions`):

```python
    merge_and_filter_transactions,
    match_transfer_pairs,
    TransferMatch,
)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_transfer_matching.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add src/finab/engine/sync.py src/finab/transactions.py tests/engine/test_transfer_matching.py
git commit -m "feat(engine): add match_transfer_pairs pure pairing pass"
```

---

### Task 5: Extract `_apply_auto_rules` (behavior-preserving refactor)

**Files:**
- Modify: `src/finab/engine/sync.py:474-558`
- Test: existing `tests/engine/test_sync_engine.py` is the guard (no new test)

- [ ] **Step 1: Run the existing suite to capture green baseline**

Run: `uv run pytest tests/engine/test_sync_engine.py -q`
Expected: PASS (baseline before refactor).

- [ ] **Step 2: Extract the rule body into `_apply_auto_rules`**

In `src/finab/engine/sync.py`, replace `_build_candidate` with a thin wrapper plus an extracted method. The extracted method is the *current* body of `_build_candidate` minus the `Candidate(...)` construction and minus the `return candidate` lines (replace each `return candidate` with bare `return`, and reference `candidate.txn` as `txn`):

```python
    def _build_candidate(self, txn) -> Candidate:
        """Construct a Candidate around `txn`, then apply auto-rules."""
        candidate = Candidate(id=txn.import_id, txn=txn)
        self._apply_auto_rules(candidate)
        return candidate

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
```

- [ ] **Step 3: Run the suite to verify no behavior change**

Run: `uv run pytest tests/engine/test_sync_engine.py -q`
Expected: PASS (same as baseline).

- [ ] **Step 4: Commit**

```bash
git add src/finab/engine/sync.py
git commit -m "refactor(engine): extract _apply_auto_rules from _build_candidate"
```

---

### Task 6: Wire matching into `SyncEngine`

**Files:**
- Modify: `src/finab/engine/sync.py:451-487` (`__init__`, `_build_candidate`)
- Modify: `tests/engine/test_sync_engine.py` (update `_build_txn` to set `fw_uuid`; add tests)

- [ ] **Step 1: Update the `_build_txn` test helper to carry `fw_uuid`**

In `tests/engine/test_sync_engine.py`, in `_build_txn`, add `fw_uuid=fw_uuid` to the `Transaction(...)` call (right after `import_id=fw_uuid,`).

- [ ] **Step 2: Write the failing tests**

```python
# add to tests/engine/test_sync_engine.py
class TestTransferMatchingInEngine:
    def _two_account_store(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        store.add_account(
            alias="Cheque",
            fw_record={"id": "fw-a", "name": "Cheque", "type": "checking", "balance": 0, "currency_code": "ZAR"},
            ynab_record={"id": "yn-a", "name": "Cheque", "type": "checking", "balance": 0, "transfer_payee_id": "tp-a"},
        )
        store.add_account(
            alias="Savings",
            fw_record={"id": "fw-b", "name": "Savings", "type": "savings", "balance": 0, "currency_code": "ZAR"},
            ynab_record={"id": "yn-b", "name": "Savings", "type": "savings", "balance": 0, "transfer_payee_id": "tp-b"},
        )
        return store

    def test_high_confidence_pair_keeps_one_suppresses_other(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        assert keep.status == "auto" and keep.auto_reason == "transfer-pair"
        assert keep.txn.payee_id == "tp-b" and keep.txn.category_id is None
        assert keep.transfer_dest_alias == "Savings"
        assert sup.status == "merged" and sup.auto_reason == "transfer-merged"
        assert keep.transfer_partner_id == sup.id and sup.transfer_partner_id == keep.id

    def test_inflow_side_not_booked_as_income(self, tmp_path):
        """Regression: the inflow rule must not claim the suppressed side."""
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[],
            ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
            store=store, tx_store=tx_store,
        )
        sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
        assert sup.auto_reason != "inflow"
        assert sup.status == "merged"

    def test_low_confidence_pair_is_pending_suggested(self, tmp_path):
        store = self._two_account_store(tmp_path)
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10", merchant_id="x")
        inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-11", merchant_id="y")
        engine = SyncEngine(
            fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
            store=store, tx_store=tx_store, transfer_match_window_days=1,
        )
        keep = next(c for c in engine.candidates if c.transfer_role == "keep")
        assert keep.status == "pending" and keep.auto_reason == "transfer-suggested"
        assert keep.txn.payee_id == "tp-b"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestTransferMatchingInEngine -v`
Expected: FAIL — `SyncEngine` has no `transfer_match_window_days` param / no match handling.

- [ ] **Step 4: Wire matching into `__init__` and `_build_candidate`**

In `src/finab/engine/sync.py`, update `SyncEngine.__init__`:

```python
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

        merged = merge_and_filter_transactions(
            fw_transactions, ynab_transactions, store, tx_store
        )
        merged.sort(key=_sort_key(store))

        # Transfer pre-pass: claim equal-and-opposite cross-account pairs
        # BEFORE per-candidate auto-rules (so the inflow side isn't booked
        # as income by rule (a)).
        self._match_by_id: dict = {}
        for mt in match_transfer_pairs(
            merged, store, window_days=transfer_match_window_days
        ):
            self._match_by_id[mt.keep_txn.import_id] = ("keep", mt)
            self._match_by_id[mt.suppress_txn.import_id] = ("suppress", mt)

        self.candidates: list[Candidate] = [
            self._build_candidate(txn) for txn in merged
        ]
```

Update `_build_candidate` to consult matches first:

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_sync_engine.py -q`
Expected: PASS (new tests + the full existing suite).

- [ ] **Step 6: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): claim transfer pairs in SyncEngine before auto-rules"
```

---

### Task 7: `confirm_transfer_match` + transfer-aware `undo`

**Files:**
- Modify: `src/finab/engine/sync.py` (`undo`, add `confirm_transfer_match`, `_reevaluate`, `_undo_transfer`)
- Test: `tests/engine/test_sync_engine.py`

- [ ] **Step 1: Write the failing tests**

```python
# add to TestTransferMatchingInEngine in tests/engine/test_sync_engine.py
def test_confirm_suggested_transfer_marks_decided(self, tmp_path):
    store = self._two_account_store(tmp_path)
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10", merchant_id="x")
    inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-11", merchant_id="y")
    engine = SyncEngine(
        fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
        store=store, tx_store=tx_store, transfer_match_window_days=1,
    )
    keep = next(c for c in engine.candidates if c.transfer_role == "keep")
    engine.confirm_transfer_match(keep.id)
    assert keep.status == "decided"
    assert keep.txn.payee_id == "tp-b"

def test_undo_transfer_reverts_both_sides(self, tmp_path):
    store = self._two_account_store(tmp_path)
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
    inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
    engine = SyncEngine(
        fw_transactions=[out, inn], ynab_transactions=[],
        ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
        store=store, tx_store=tx_store,
    )
    keep = next(c for c in engine.candidates if c.transfer_role == "keep")
    sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
    engine.undo(keep.id)
    # Both lose their transfer role; suppress side re-enters normal rules.
    assert keep.transfer_role is None and sup.transfer_role is None
    assert keep.status == "pending"          # outflow, no merchant → no-merchant
    assert sup.status == "auto" and sup.auto_reason == "inflow"   # inflow reclaimed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestTransferMatchingInEngine -v`
Expected: FAIL — no `confirm_transfer_match`; `undo` rejects non-decided.

- [ ] **Step 3: Implement confirm + transfer undo**

In `src/finab/engine/sync.py`, add methods to `SyncEngine` (place near `undo`):

```python
    def confirm_transfer_match(self, candidate_id: str) -> None:
        """Accept a low-confidence suggested transfer: pending -> decided.
        The txn's transfer payee was already set at build time."""
        c = self._candidate(candidate_id)
        if c.transfer_role != "keep" or c.auto_reason != "transfer-suggested":
            raise ValueError(
                f"candidate {candidate_id!r} is not a suggested transfer"
            )
        c.prior_state = self._snapshot(c.txn)
        c.status = "decided"

    def _reevaluate(self, candidate: "Candidate") -> None:
        """Reset a candidate's txn to a neutral state and re-run auto-rules.
        Used after un-matching a transfer."""
        candidate.txn.payee_id = None
        candidate.txn.payee_name = None
        candidate.txn.category_id = None
        candidate.txn.subtransactions = []
        candidate.status = "pending"
        candidate.auto_reason = None
        candidate.warnings = []
        self._apply_auto_rules(candidate)

    def _undo_transfer(self, c: "Candidate") -> None:
        """Revert a matched transfer (either side): drop the match and
        re-evaluate both sides as normal candidates."""
        keep = c if c.transfer_role == "keep" else self._candidate(c.transfer_partner_id)
        suppress = self._candidate(keep.transfer_partner_id)
        if keep.status == "flushed" or suppress.status == "flushed":
            raise ValueError("cannot undo a flushed transfer")
        self._match_by_id.pop(keep.txn.import_id, None)
        self._match_by_id.pop(suppress.txn.import_id, None)
        for cand in (keep, suppress):
            cand.transfer_role = None
            cand.transfer_partner_id = None
            cand.transfer_dest_alias = None
            cand.prior_state = None
            self._reevaluate(cand)
```

Update `undo` to dispatch transfers first. The method currently begins:

```python
    def undo(self, candidate_id: str) -> None:
        """..."""
        c = self._candidate(candidate_id)
        if c.status != "decided":
```

Insert the transfer dispatch immediately after the `c = self._candidate(candidate_id)` line (i.e. before the existing `if c.status != "decided":`):

```python
        if c.transfer_role in ("keep", "suppress"):
            self._undo_transfer(c)
            return
```

Leave the rest of `undo` exactly as-is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_sync_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): confirm + undo for matched transfers"
```

---

### Task 8: `flush` records suppressed counterpart

**Files:**
- Modify: `src/finab/engine/sync.py` (`flush`)
- Test: `tests/engine/test_sync_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# add to TestTransferMatchingInEngine
def test_flush_records_suppressed_side_and_marks_flushed(self, tmp_path):
    store = self._two_account_store(tmp_path)
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    out = _build_txn(fw_uuid="o", amount=-50000, account_id="fw-a", date_str="2026-05-10")
    inn = _build_txn(fw_uuid="i", amount=50000, account_id="fw-b", date_str="2026-05-10")
    engine = SyncEngine(
        fw_transactions=[out, inn], ynab_transactions=[],
        ynab_categories=[_FakeCategory("cat-rta", "Inflow: Ready to Assign")],
        store=store, tx_store=tx_store,
    )
    keep = next(c for c in engine.candidates if c.transfer_role == "keep")
    sup = next(c for c in engine.candidates if c.transfer_role == "suppress")
    client = _FakeYnabClient()
    engine.flush(client, budget_id="bid")
    # Only the keep side was pushed (one create); suppressed side never sent.
    assert len(client.created) == 1 and len(client.created[0]) == 1
    assert client.created[0][0] is keep.txn
    # Suppressed FW uuid now maps to the kept side's import_id.
    assert tx_store.import_id_for("i") == keep.txn.import_id
    assert keep.status == "flushed" and sup.status == "flushed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/engine/test_sync_engine.py::TestTransferMatchingInEngine::test_flush_records_suppressed_side_and_marks_flushed -v`
Expected: FAIL — suppressed side not recorded; `import_id_for("i")` returns its own rotated id, not the keep's.

- [ ] **Step 3: Record suppressions after a successful push**

In `src/finab/engine/sync.py`, append to `flush` after the updates block:

```python
        if updates:
            ynab_client.update_transactions(budget_id, [c.txn for c in updates])
            for c in updates:
                c.status = "flushed"

        # Now that each kept side is on YNAB, point its suppressed counterpart
        # at the kept side's import_id so dedup's "transfer twin resolved" path
        # skips it forever (two FW uuids -> one import_id; see spec).
        for c in creates + updates:
            if getattr(c, "transfer_role", None) == "keep" and c.transfer_partner_id:
                partner = self._candidate(c.transfer_partner_id)
                if partner.status == "merged":
                    self._tx_store.record(partner.txn.fw_uuid, c.txn.import_id)
                    partner.status = "flushed"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/engine/test_sync_engine.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): record suppressed transfer side on flush"
```

---

### Task 9: Config knob (`transfer_match_window_days`) + wiring + settings display

**Files:**
- Modify: `src/finab/config.py`
- Modify: `src/finab/tui/screens/sync.py:75-101` (`bind_data`)
- Modify: `src/finab/tui/screens/settings.py`
- Test: `tests/test_helpers.py` or a new `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py (create)
import finab.config as config


def test_window_days_defaults_to_one(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    assert config.load_transfer_match_window_days() == 1


def test_window_days_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    config.save_transfer_match_window_days(3)
    assert config.load_transfer_match_window_days() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `module 'finab.config' has no attribute 'load_transfer_match_window_days'`.

- [ ] **Step 3: Add config helpers**

In `src/finab/config.py`, after `save_budget_id`:

```python
def load_transfer_match_window_days() -> int:
    """Day window for pairing transfer sides. Defaults to 1."""
    data = _load_data()
    value = data.get("transfer_match_window_days", 1)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1


def save_transfer_match_window_days(days: int) -> None:
    data = _load_data()
    data["transfer_match_window_days"] = int(days)
    _save_data(data)
```

- [ ] **Step 4: Run config test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Wire the value into the engine and settings display**

In `src/finab/tui/screens/sync.py`, inside `bind_data`, pass the knob to the engine:

```python
        from finab.config import load_transfer_match_window_days
        self._engine = SyncEngine(
            fw_transactions=loaded.fw_transactions,
            ynab_transactions=loaded.ynab_transactions,
            ynab_categories=loaded.ynab_categories,
            store=store,
            tx_store=tx_store,
            transfer_match_window_days=load_transfer_match_window_days(),
        )
```

In `src/finab/tui/screens/settings.py`, add a Static in `compose` (after `settings-paths`):

```python
        yield Static("", id="settings-transfer")
```

And at the end of `_update_content`:

```python
        from finab.config import load_transfer_match_window_days
        self.query_one("#settings-transfer", Static).update(
            f"  Transfer match window: ±{load_transfer_match_window_days()} day(s)"
            f"  (edit transfer_match_window_days in config.json)"
        )
```

- [ ] **Step 6: Run the TUI + engine suites**

Run: `uv run pytest tests/test_config.py tests/tui -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/finab/config.py src/finab/tui/screens/sync.py src/finab/tui/screens/settings.py tests/test_config.py
git commit -m "feat(config): transfer_match_window_days knob + settings display"
```

---

### Task 10: PendingList glyphs for transfer states

**Files:**
- Modify: `src/finab/tui/widgets/pending_list.py:19-45`
- Modify: `src/finab/tui/styles.tcss:119-126`
- Test: `tests/tui/` (find the existing pending_list test; else create `tests/tui/test_pending_list_glyphs.py`)

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_pending_list_glyphs.py (create)
from finab.engine.sync import Candidate
from finab.tui.widgets.pending_list import _glyph_for, _glyph_class_for


def _c(status, reason):
    return Candidate(id="x", txn=object(), status=status, auto_reason=reason)


def test_transfer_pair_glyph():
    c = _c("auto", "transfer-pair")
    assert _glyph_for(c) == "⇄"
    assert _glyph_class_for(c) == "glyph-auto-transfer"


def test_transfer_suggested_glyph():
    c = _c("pending", "transfer-suggested")
    assert _glyph_for(c) == "⇄"
    assert _glyph_class_for(c) == "glyph-transfer-suggested"


def test_transfer_merged_glyph():
    c = _c("merged", "transfer-merged")
    assert _glyph_for(c) == "⊝"
    assert _glyph_class_for(c) == "glyph-merged"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_pending_list_glyphs.py -v`
Expected: FAIL — these keys aren't in `_GLYPHS` (fallback `?`).

- [ ] **Step 3: Add glyph + class mappings**

In `src/finab/tui/widgets/pending_list.py`, add to `_GLYPHS`:

```python
    ("auto", "transfer-pair"): "⇄",        # ⇄
    ("pending", "transfer-suggested"): "⇄", # ⇄
    ("merged", "transfer-merged"): "⊝",     # ⊝
    ("merged", None): "⊝",
```

Add to `_GLYPH_CSS_CLASS`:

```python
    ("auto", "transfer-pair"): "glyph-auto-transfer",
    ("pending", "transfer-suggested"): "glyph-transfer-suggested",
    ("merged", "transfer-merged"): "glyph-merged",
    ("merged", None): "glyph-merged",
```

In `src/finab/tui/styles.tcss`, after the existing `glyph-*` rules:

```css
Label.glyph-transfer-suggested { color: $warning; }
Label.glyph-merged { color: $text-muted; }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_pending_list_glyphs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/tui/widgets/pending_list.py src/finab/tui/styles.tcss tests/tui/test_pending_list_glyphs.py
git commit -m "feat(tui): pending-list glyphs for transfer-pair/suggested/merged"
```

---

### Task 11: TransactionCard renders transfer states + destination

**Files:**
- Modify: `src/finab/tui/widgets/transaction_card.py:25-30, 152-162`
- Test: extend the test from Task 10's dir or create `tests/tui/test_transaction_card_transfer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_transaction_card_transfer.py (create)
from datetime import date

from finab.engine.sync import Candidate
from finab.models import Transaction
from finab.tui.widgets.transaction_card import TransactionCard


def _candidate(status, reason, dest):
    txn = Transaction(account_id="yn-a", date=date(2026, 5, 10), amount=-50000, memo="m")
    return Candidate(id="x", txn=txn, status=status, auto_reason=reason,
                     transfer_role="keep", transfer_dest_alias=dest)


def test_transfer_pair_status_label():
    card = TransactionCard()
    label = card._status_label(_candidate("auto", "transfer-pair", "Savings"))
    assert "TRANSFER-PAIR" in label
    assert "Savings" in label


def test_merged_status_label():
    card = TransactionCard()
    label = card._status_label(_candidate("merged", "transfer-merged", "Savings"))
    assert label.startswith("MERGED")
    assert "Savings" in label
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_transaction_card_transfer.py -v`
Expected: FAIL — no `_status_label`; `_STATUS_LABELS` lacks `merged`.

- [ ] **Step 3: Add `merged` label + extract `_status_label` with destination**

In `src/finab/tui/widgets/transaction_card.py`, extend `_STATUS_LABELS`:

```python
_STATUS_LABELS = {
    "pending": "PENDING",
    "decided": "DECIDED",
    "auto": "AUTO",
    "flushed": "FLUSHED",
    "merged": "MERGED",
}
```

Add a `status-merged` CSS rule inside `DEFAULT_CSS` (next to the other `#card-status.status-*` lines):

```css
    #card-status.status-merged { color: $text-muted; }
```

Add a pure helper method and use it in `set_candidate`:

```python
    def _status_label(self, candidate: Candidate) -> str:
        label = _STATUS_LABELS.get(candidate.status, candidate.status.upper())
        if candidate.auto_reason:
            label = f"{label} · {candidate.auto_reason.upper()}"
        if candidate.transfer_dest_alias:
            label = f"{label} ↦ {candidate.transfer_dest_alias}"
        return label
```

In `set_candidate`, replace the status-label construction:

```python
        status_widget = self.query_one("#card-status", Static)
        status_widget.update(self._status_label(candidate))
        for cls in ("status-pending", "status-decided", "status-auto",
                    "status-flushed", "status-warning", "status-merged"):
            status_widget.remove_class(cls)
        if candidate.warnings:
            status_widget.add_class("status-warning")
        else:
            status_widget.add_class(f"status-{candidate.status}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/tui/test_transaction_card_transfer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/finab/tui/widgets/transaction_card.py tests/tui/test_transaction_card_transfer.py
git commit -m "feat(tui): transaction card renders transfer/merged states + destination"
```

---

### Task 12: Sync screen — `t` accepts a suggestion; refresh merged rows

**Files:**
- Modify: `src/finab/tui/screens/sync.py:253-268` (`action_force_transfer`), `400-411` (`_refresh_after_decision`)
- Test: `tests/tui/test_sync_screen.py` (find existing; extend) or create `tests/tui/test_sync_transfer_actions.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/tui/test_sync_transfer_actions.py (create)
from pathlib import Path
from datetime import date

from finab.engine.sync import SyncEngine
from finab.models import Transaction
from finab.store import ConfigStore
from finab.transactions import TransactionsStore
from finab.tui.screens.sync import SyncScreen


def _engine(tmp_path: Path):
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Cheque",
        fw_record={"id": "fw-a", "name": "Cheque", "type": "checking", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-a", "name": "Cheque", "type": "checking", "balance": 0, "transfer_payee_id": "tp-a"},
    )
    store.add_account(
        alias="Savings",
        fw_record={"id": "fw-b", "name": "Savings", "type": "savings", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-b", "name": "Savings", "type": "savings", "balance": 0, "transfer_payee_id": "tp-b"},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    out = Transaction(import_id="o", fw_uuid="o", amount=-50000, date=date(2026, 5, 10),
                      account_id="yn-a", merchant_id="x", memo="m")
    inn = Transaction(import_id="i", fw_uuid="i", amount=50000, date=date(2026, 5, 11),
                      account_id="yn-b", merchant_id="y", memo="m")
    return SyncEngine(
        fw_transactions=[out, inn], ynab_transactions=[], ynab_categories=[],
        store=store, tx_store=tx_store, transfer_match_window_days=1,
    ), store


def test_accept_suggested_transfer_confirms_without_picker(tmp_path):
    """If the current candidate is a suggested transfer, `t` confirms it
    in-place rather than opening the account picker."""
    engine, store = _engine(tmp_path)
    keep = next(c for c in engine.candidates if c.transfer_role == "keep")

    screen = SyncScreen.__new__(SyncScreen)   # avoid Textual mount
    screen._engine = engine
    screen._store = store
    opened = {"picker": False}
    screen._current_candidate = lambda: keep
    screen._refresh_after_decision = lambda cid: None
    # Stub the picker path so we can assert it is NOT taken.
    def _fail_picker(*a, **k):
        opened["picker"] = True
    screen._open_force_transfer_picker = _fail_picker

    screen.action_force_transfer()
    assert keep.status == "decided"
    assert opened["picker"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/tui/test_sync_transfer_actions.py -v`
Expected: FAIL — `action_force_transfer` always opens the picker; no `_open_force_transfer_picker`.

- [ ] **Step 3: Make `action_force_transfer` context-aware**

In `src/finab/tui/screens/sync.py`, refactor `action_force_transfer` to branch on a suggested candidate, extracting the existing picker body into `_open_force_transfer_picker`:

```python
    def action_force_transfer(self) -> None:
        """On a suggested transfer, confirm the pre-computed pair. Otherwise
        open the manual account picker (one-sided / undetected transfers)."""
        c = self._current_candidate()
        if c is None or self._engine is None or self._store is None:
            return
        if c.transfer_role == "keep" and c.auto_reason == "transfer-suggested":
            self._engine.confirm_transfer_match(c.id)
            self._refresh_after_decision(c.id)
            return
        self._open_force_transfer_picker(c)

    def _open_force_transfer_picker(self, c) -> None:
        from finab.tui.widgets.account_link_picker import AccountLinkPicker
        modal = AccountLinkPicker(store=self._store, title="Force transfer to which account?")

        def _on_picked(transfer_payee_id):
            if transfer_payee_id is None:
                return
            self._engine.apply_transfer(c.id, transfer_payee_id=transfer_payee_id)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)
```

- [ ] **Step 4: Ensure undo refreshes both sides**

In `src/finab/tui/screens/sync.py`, update `action_undo` so a transfer undo redraws the partner row too:

```python
    def action_undo(self) -> None:
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        partner_id = getattr(c, "transfer_partner_id", None)
        try:
            self._engine.undo(c.id)
        except ValueError:
            self.app.bell()
            return
        self._refresh_after_decision(c.id)
        if partner_id:
            pl = self.query_one("#sync-pending", PendingList)
            pl.refresh_row(partner_id)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/tui/test_sync_transfer_actions.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 7: Commit**

```bash
git add src/finab/tui/screens/sync.py tests/tui/test_sync_transfer_actions.py
git commit -m "feat(tui): t accepts suggested transfer; undo refreshes both sides"
```

---

## Final Verification

- [ ] **Run the entire test suite**

Run: `uv run pytest -q`
Expected: all PASS.

- [ ] **Smoke-run the app against live data (manual)**

Run: `uv run finab`
Expected: on the Sync screen, real transfer pairs (e.g. FNB Easy → Discovery "Me/Me") show as `⇄` auto rows with the counterpart greyed `⊝ merged into transfer ↦ …`; ambiguous ones show as amber suggestions that `t` confirms. No duplicate transfers after flush.

---

## Self-Review Notes

- **Spec coverage:** pagination (T1), fw_uuid (T2), Candidate fields/states (T3), matcher + confidence + tie-break (T4), pre-pass ordering vs inflow rule (T5–T6), suggest/confirm (T7), undo-both + re-evaluate (T7), suppression dedup mapping (T8), config knob ±1 + settings (T9), UI glyphs/card/keys (T10–T12), keep manual `t` fallback (T12). `is_transfer` deliberately unused — no task, by design.
- **Creates-only refinement** (over the spec's "creates + uncategorized updates") is called out in *File Structure* and enforced in T4's `match_transfer_pairs` pool filter and `test_already_in_ynab_is_excluded`.
- **Type consistency:** `TransferMatch.confidence` is the string `"high"`/`"low"` throughout; `transfer_role` is `"keep"`/`"suppress"`; new `auto_reason`s and `merged` status are added to the Literals in T3 before first use in T6.
