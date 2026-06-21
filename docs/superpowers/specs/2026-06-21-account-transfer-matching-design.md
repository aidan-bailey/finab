# Account-Transfer Matching — Design

**Status:** Approved (design phase)
**Date:** 2026-06-21
**Scope:** Detect transfers between the user's own accounts automatically by pairing an outflow on one account with an equal-and-opposite inflow on another, near the same date. Push **one** side as a YNAB transfer (YNAB auto-creates the mirror) and **suppress** the FinWise counterpart so the transfer isn't double-counted. A confidence model decides which pairs auto-apply and which are surfaced as suggestions needing confirmation. Includes a prerequisite fix to FinWise transaction pagination.
**Out of scope:** Cross-sync-batch reconciliation (both sides must appear in the same sync run to auto-match — see *Known Limitations*). Matching against the YNAB-side auto-created mirror by content. Removing the manual `t` force-transfer (it stays as the fallback for one-sided transfers).

## Motivation

A transfer between two of the user's own accounts appears in FinWise as **two** transactions: an outflow (−X) on account A and an inflow (+X) on account B. In YNAB, a transfer is a **single** entity: pushing one side with a *transfer payee* makes YNAB auto-create the linked counterpart in the other account. So Finab must push exactly one side and suppress the other, or the transfer is duplicated.

Today, transfers are detected only via the **merchant → transfer-payee** linkage (`_build_candidate` rule (b), `engine/sync.py:507`). When no merchant is linked, the user manually presses `t` (`action_force_transfer`, `sync.py:253`) on each side. This is tedious, and worse, it doesn't suppress the counterpart — so a user who forces a transfer on the outflow then sees the YNAB-created mirror **and** the still-unsynced FinWise inflow, producing a duplicate.

This feature replaces the manual common case with automatic pair detection.

### Signal investigation (against live data, all 1,067 transactions)

The matching signal was chosen empirically, not assumed. Findings:

- **`is_transfer` flag is unusable as a gate.** Of 19 genuine same-day transfer pairs, only **1** had the flag on both sides; **14** had it on neither. Only 20/1067 transactions carry the flag at all, and 12 of those are external deposits with no opposite partner. Gating on `is_transfer` would catch ~1 of 19 transfers.
- **`merchant_id` is unusable as a primary matcher.** The merchant_ids that appear on transfers are overwhelmingly generic — e.g. `c1a0452a` appears 144 times but is a transfer only twice. `merchant_name` is null everywhere. merchant_id **cannot identify** a transfer and **cannot pair** sides. (This is also *why* the merchant→transfer-payee rule fails for this user: there is no clean "this merchant = transfer to account X" mapping to make.) However, **11/19 pairs share a merchant_id**, so a *shared* merchant_id is a useful tie-breaker / confidence signal — never a requirement.
- **amount + sign + own-accounts + date is the reliable signal.** It found 19 same-day pairs; widening to **±1 day** caught **+5** real inter-bank next-day settlements for only +1 ambiguous case. ±3 days added only +2 more. The 8–10 "ambiguous" cases (multiple same-amount candidates on one day) are why matched pairs must stay reviewable/undoable.

| Signal | Primary matcher? | Role in design |
|---|---|---|
| `is_transfer` | ✗ (1/19 both-sided) | ignored (b2 warning retained) |
| `merchant_id` | ✗ (generic; 144-use ids) | tie-breaker / confidence only |
| amount + sign + own-accounts + date | ✓ (19 same-day, 24 at ±1) | **primary key** |

## Prerequisite: FinWise pagination fix

`FinWiseClient.get_transactions()` (`client.py:40`) calls `self._client._transport.get("/transactions")` with no pagination, returning only the **latest 100** of 1,067 transactions. This is a pre-existing bug: if more than 100 transactions accumulate between syncs, older ones are silently never imported. It is also a hard blocker for this feature — both sides of a transfer must be visible in one sync.

The FinWise API paginates via a **JSON-encoded query parameter**:

```
GET /transactions?pagination={"pageNumber":1,"pageSize":500}
```

(The bundled `finwise-python` SDK sends bare `pageNumber`/`pageSize`, which the API's strict schema rejects with `unrecognized_keys` — confirmed live. Response headers `x-count`, `x-has-next-page`, `x-page-number`, `x-page-size` describe the page.)

### Fix

`get_transactions` loops pages until exhausted. The existing transport's `get()` returns the parsed JSON body and discards headers, so termination is by short page (a page returning fewer than `pageSize` rows is the last), not by reading `x-has-next-page`:

```python
def get_transactions(self, start_date=None, end_date=None):
    PAGE_SIZE = 500
    finwise_txns = []
    page = 1
    while True:
        batch = self._client._transport.get(
            "/transactions",
            params={"pagination": json.dumps({"pageNumber": page, "pageSize": PAGE_SIZE})},
        )
        if not isinstance(batch, list):
            raise ValueError(f"Unexpected response format from FinWise API: {type(batch)}")
        finwise_txns.extend(FinWiseTransaction.model_validate(t) for t in batch)
        if len(batch) < PAGE_SIZE:
            break
        page += 1
    # date filtering + conversion unchanged
```

Date filtering and `Transaction.from_finwise` conversion are unchanged.

## Matching pre-pass

A new **pure** function in `engine/sync.py`, run from `SyncEngine.__init__` *after* `merge_and_filter_transactions` + sort, but *before* the per-candidate `_build_candidate` pass. Ordering matters: the inflow side of a transfer is a positive amount and would otherwise be claimed by the inflow→"Ready to Assign" rule (a) (`sync.py:490`), which `return`s before the transfer rule — a latent bug this pre-pass also fixes.

```python
def match_transfer_pairs(candidates_txns, store, *, window_days=1):
    """Return a list of TransferMatch(keep_txn, suppress_txn, dest_transfer_payee_id,
    dest_alias, confidence) for equal-and-opposite cross-account pairs."""
```

**Pool.** Transactions in the current sync batch (the merged needs-sync set) that are on active accounts (mapped, `ignore_transactions` false) with nonzero amount. After `merge_and_filter_transactions`, `txn.account_id` is the YNAB account id; map back to the stored account via `store.account_by_ynab_id` to read its `ynab.transfer_payee_id`.

**Match.** For each outflow (`amount < 0`), find inflow candidates where `inflow.amount == -outflow.amount`, on a *different* account, with `abs((inflow.date - outflow.date).days) <= window_days`, not already consumed, **and whose destination (the inflow's) account has a `transfer_payee_id`**. Rank candidates by:

1. shared `merchant_id` (outflow.merchant_id == inflow.merchant_id) first,
2. then closest date,
3. then a stable order (e.g. inflow import_id) for determinism.

Consume the best one-to-one (each transaction used in at most one pair).

**Keep / suppress.** Keep the **outflow** (the source); set its payee to the **destination** (inflow's) account's `transfer_payee_id` → YNAB creates the +X mirror on the destination. Suppress the inflow.

**Confidence.**
- **HIGH** — exactly one candidate partner existed **and** (same-day **or** shared merchant_id).
- **LOW** — more than one candidate partner existed, **or** the chosen partner is ±1 day away with a *different* merchant_id.

## Engine representation

### New `AutoReason` values

```python
AutoReason = Literal["inflow", "transfer", "no-merchant", "pre-month",
                     "transfer-pair", "transfer-suggested", "transfer-merged"]
```

- `transfer-pair` — kept side, HIGH confidence → `status="auto"`.
- `transfer-suggested` — kept side, LOW confidence → `status="pending"` (won't flush until confirmed).
- `transfer-merged` — suppressed side (see new status below).

### New `CandidateStatus`

```python
CandidateStatus = Literal["pending", "auto", "decided", "flushed", "merged"]
```

- `merged` — the suppressed counterpart. Never pushed by `flush()`. Rendered greyed as *"merged into transfer ↦ &lt;dest&gt;"*.

### `Candidate` new fields

```python
transfer_partner_id: Optional[str] = None   # links the two sides (by candidate id)
transfer_role: Optional[Literal["keep", "suppress"]] = None
transfer_dest_alias: Optional[str] = None    # destination account alias, for display
```

### `_build_candidate` integration

`SyncEngine.__init__` builds a lookup `txn id → TransferMatch` from the pre-pass, then `_build_candidate` consults it **first**:

- If the txn is a match's **keep** side: set `payee_id = dest_transfer_payee_id`, `payee_name=None`, clear category/subtransactions; `status = "auto"` (HIGH) or `"pending"` (LOW); `auto_reason = "transfer-pair"` / `"transfer-suggested"`; set `transfer_*` fields.
- If the txn is a match's **suppress** side: `status="merged"`, `auto_reason="transfer-merged"`, `transfer_*` fields set. No payee/category mutation (it won't be pushed).
- Otherwise: existing rules (a)–(d) run unchanged.

## Flush + dedup

### Flush

`flush()` already pushes only `decided` + `auto`. `merged` is excluded automatically — no change to the create/update split. The kept side pushes as a normal transfer (payee_id = a transfer payee → YNAB creates the mirror).

After a kept side's batch flushes successfully, record its suppressed partner in the transactions store so dedup skips it forever:

```python
# for each flushed keep-side candidate c with a suppress partner s:
tx_store.record(s.txn._fw_uuid, c.txn.import_id)   # two FW uuids → one import_id
```

This requires the suppressed candidate's original FinWise uuid. `merge_and_filter_transactions` currently overwrites `txn.import_id` with our durable id; the suppressed side is *not* pushed, so it needs its FinWise uuid preserved. Capture it (e.g. stash the original `fw_uuid` on the txn) before the import_id is rotated, for suppressed candidates.

### Dedup mechanism (two FW uuids → one import_id)

The suppressed FinWise inflow is mapped to the **kept side's** import_id. On the next sync, `merge_and_filter_transactions` looks up the suppressed FW uuid, finds the kept side's import_id, sees it is present in YNAB **and** is a transfer (`transfer_account_id` set), and takes the existing `skip_categorized` path (`sync.py:195`). Both FW uuids resolve to the one YNAB transfer.

Rationale for reusing the import_id rather than a separate "suppressed" set: the two FinWise rows *are* the two halves of one YNAB transfer, and this routes through the existing, well-tested "transfer twin already resolved" branch with no new dedup code. The YNAB-created mirror has `import_id=None` and cannot be mapped to directly. This is a deliberate, documented exception to the usual one-uuid-one-import_id invariant.

Sequencing note: `merge_and_filter_transactions` runs *before* the matching pre-pass and will have already assigned the suppressed inflow its own fresh import_id (as a normal "new" row added to the batch). The flush-time `tx_store.record(suppressed_fw_uuid, kept_import_id)` **overwrites** that mapping. If the user quits before flushing, the suppressed side keeps its own (never-pushed) import_id, which `prune_stale` drops on the next sync — so it simply re-enters matching next time. No corruption either way.

`prune_stale` keeps both mappings as long as the shared import_id is live; if the user deletes the transfer in YNAB, both are pruned together and both FinWise rows re-import on the next sync (correct).

## Sync UI

`tui/screens/sync.py` + `tui/widgets/`:

- **Kept HIGH** (`transfer-pair`, auto): green, shown `↦ &lt;dest&gt;` like existing auto rows.
- **Kept LOW** (`transfer-suggested`, pending): shown *"suggested transfer ↦ &lt;dest&gt;"*; does not flush until confirmed.
- **Suppressed** (`transfer-merged`, merged): greyed, *"merged into transfer ↦ &lt;dest&gt;"*.

### Keys

- **`t`** becomes context-aware (`action_force_transfer`): if the current candidate is `transfer-suggested`, `t` **accepts** the pre-computed pair — kept side `pending → decided` (keeping its payee/dest), suppressed partner confirmed. Otherwise `t` opens the manual `AccountLinkPicker` exactly as today (the one-sided fallback).
- **`u`** (`action_undo`) on either side of a match reverts it: kept side returns to its pre-match state (re-run the normal rules → pending/inflow/etc.), suppressed side returns from `merged` to a normal candidate. Both sides revert together via `transfer_partner_id`.
- All other keys unchanged.

`SyncEngine` gains methods mirroring the existing apply/undo pattern: `confirm_transfer_match(candidate_id)` and `undo` extended to handle `merged`/transfer-pair candidates (reverting both partners).

## Config knob

`transfer_match_window_days` (default `1`), stored top-level in `config.json` alongside `budget_id`, with `load_*`/`save_*` helpers in `config.py` mirroring `load_budget_id`. Surfaced as an editable setting on the Settings screen. `SyncEngine.__init__` reads it and passes it to `match_transfer_pairs`.

## File Changes Summary

| File | Change |
|---|---|
| `src/finab/client.py` | `get_transactions` paginates via `pagination={"pageNumber","pageSize"}` JSON param, looping until a short page. |
| `src/finab/engine/sync.py` | New `match_transfer_pairs` pure function + `TransferMatch` dataclass. `SyncEngine.__init__` runs it before the build pass and threads `transfer_match_window_days`. New `AutoReason`/`CandidateStatus` values and `Candidate.transfer_*` fields. `_build_candidate` consults matches first. `flush` records suppressed→kept import_id mappings after successful push. New `confirm_transfer_match`; `undo` handles transfer pairs. Preserve original FW uuid on suppressed txns. |
| `src/finab/transactions.py` | Re-export `match_transfer_pairs` / `TransferMatch` for parity with other engine helpers. |
| `src/finab/config.py` | `load_transfer_match_window_days` / `save_transfer_match_window_days` (default 1). |
| `src/finab/tui/screens/sync.py` | `action_force_transfer` accepts a suggested pair when present; `action_undo` reverts both sides; render merged/suggested states. |
| `src/finab/tui/widgets/transaction_card.py`, `pending_list.py` | Render `transfer-pair` / `transfer-suggested` / `transfer-merged` states (glyphs + dest alias). |
| `src/finab/tui/screens/settings.py` | Edit `transfer_match_window_days`. |
| `tests/` | See Testing. |

## Testing

- **`match_transfer_pairs` (pure, primary coverage):** same-day exact pair → HIGH; ±1-day same merchant → HIGH; ±1-day different merchant → LOW; two same-amount inflows same day → both candidates → LOW + deterministic pick (shared merchant_id wins, else closest date); outflow whose destination account has no `transfer_payee_id` → no match; same-account equal/opposite → no match; ignored/unmapped account → excluded; one-to-one consumption (one inflow can't satisfy two outflows).
- **Engine:** matched pair yields keep (`auto`/`pending`) + suppress (`merged`) candidates with linked `transfer_partner_id`; the inflow side is **not** booked as "Ready to Assign" (regression for the rule-(a) ordering bug); `confirm_transfer_match` moves a suggested pair to decided; `undo` reverts both sides.
- **Flush + dedup:** after flushing a kept side, the suppressed FW uuid maps to the kept import_id; a second `merge_and_filter_transactions` pass skips both (uses `TransactionsStore(tmp_path)` per the conftest contract).
- **Pagination:** `get_transactions` issues sequential page requests and concatenates; stops on a short page (mock `_transport.get`).

## Known Limitations

- **Same-batch requirement.** Both sides must appear in one sync run to auto-match. With frequent syncing they nearly always do (transfers settle same/next day). A straggler whose partner was synced previously falls back to manual `t`. Cross-sync reconciliation is deliberately out of scope for v1.
- **Already-mis-booked inflows.** Transfers whose inflow side was booked as income before this feature shipped are already resolved in YNAB and won't be re-matched; the user corrects those manually. Going forward the pre-pass prevents the mis-booking.
- **Creates + uncategorized updates only.** Matching operates on the current needs-sync batch; it does not retroactively convert already-categorized YNAB transactions into transfers.

## Non-Goals

- Removing manual `t` (kept as the one-sided fallback).
- Using `is_transfer` or `merchant_id` as a matching gate (evidence shows both are unreliable; merchant_id is a tie-breaker only).
- Fuzzy amount matching (transfers are exact equal-and-opposite; fees are separate transactions).
- Tolerance windows beyond the configurable day count.
