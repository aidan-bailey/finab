# Lifecycle + enablers — design

Date: 2026-06-21
Status: approved (design); pending implementation plan

## Context

Finab is a Textual TUI that syncs FinWise transactions into YNAB. A
"functionality sweep" surfaced a cluster of gaps where the app cannot
fully bootstrap or refresh itself, plus small tech-debt items that
unblock them. This is **sub-project 1 of 3** from that sweep; the other
two (Smarter memory `D1`, Sync power features `C1–C4`) are deferred to
their own spec → plan → implement cycles.

This sub-project bundles:

- **A1** — first-run budget picker (none exists today; `app.py:128`
  skips all data loading when `budget_id` is falsy, so a fresh config is
  a dead-end blank screen).
- **A2** — in-app refresh / re-sync (`load_all` runs once on mount; no
  binding re-fetches, so seeing new state requires a restart).
- **A3** — trigger reconcile-to-YNAB from the TUI (the
  `_reconcile_store_*_to_ynab` helpers exist and are unit-tested as
  callable, but nothing invokes them at runtime).
- **B1** — Settings-reachable budget switching (folded in because the A1
  modal makes it nearly free; closes the read-only Settings gap).
- **F1** — replace the `TODO(plan-2)` `print()`s in the reconcile
  helpers with a structured return value (prerequisite for A3).
- **F2** — parallelize `load_all`'s seven sequential fetches.
- **F3** — remove the dead `PlaceholderScreen`.

## Decisions (from brainstorming)

1. **Refresh vs. unflushed decisions → Guard.** When a refresh is
   triggered while the Sync engine holds `decided`/`auto` candidates that
   aren't on YNAB yet, block the refresh and present a flush-or-discard
   prompt. Consistent with the existing `action_quit_with_confirm`
   (`app.py:340`). No silent decision loss, no surprise writes.
2. **Refresh trigger → manual key only** (`ctrl+r`; plain `r` is
   repeat-history). No auto-after-flush, no timer.
3. **Reconcile → manual action per screen, with structured results.**
   Reconcile writes to YNAB (creates missing accounts/payees), so it is
   never automatic. A binding on the Accounts and Merchants screens runs
   it and surfaces a created/skipped/failed summary.
4. **Budget switching → include B1.** Wire the first-run picker into the
   Settings screen so the active budget can be changed later.

## Architecture approach

**Single (re)load path.** Both boot and refresh funnel through the
existing `_kickoff_load` worker (`app.py:170`), which already fetches all
data *and* re-binds every screen (rebuilding the Sync engine from fresh
data). Refresh therefore becomes "guard, then call `_kickoff_load`
again."

An incremental "preserve in-flight decisions across reload" path was
considered and rejected: the **Guard** decision resolves pending
decisions *before* reloading, so a clean rebuild is always safe and much
simpler.

## Design

### 1 · Budget bootstrap & switching (A1 + B1)

- New `BudgetPickerModal` in `tui/widgets/budget_picker.py` — a
  `ModalScreen` listing budgets from `ynab_client.get_budgets()`,
  filterable like the existing pickers, returning a budget id or `None`
  on cancel.
- Boot fetches the budget list once (a single `get_plans()` call) and
  caches it on the app (also feeds Settings/B1 without re-fetch). Then:
  - `budget_id` missing **or stale** (not in the fetched list) → open the
    picker. Exactly one budget → auto-select + `save_budget_id`, no
    prompt.
  - On selection → `save_budget_id`, set `self._budget_id`,
    `_kickoff_load()`. Cancel with no valid budget → error banner
    prompting to pick one (reopenable).
- **B1:** Settings screen gains a *switch budget* binding that reopens
  the same modal from the cache; on pick → save + trigger the guarded
  refresh path.

### 2 · Manual refresh with guard (A2)

- Global binding **`ctrl+r`** → `action_refresh`.
- Guard: if the decided/auto count > 0, push a flush-or-discard prompt
  (reuse/extend `FlushConfirmModal` to offer **flush / discard /
  cancel**): flush → flush then reload; discard → reload; cancel → abort.
- Reload re-runs `_kickoff_load` (fresh fetch, fresh engine, all screens
  re-bound).
- `ctrl+r` is global; `check_action` returns it visible on every screen.

### 3 · Reconcile from TUI + structured results (A3 + F1)

- **F1:** `_reconcile_store_accounts_to_ynab` and
  `_reconcile_store_merchants_to_ynab` return a `ReconcileResult`
  dataclass instead of `int` + `print()`:
  - `created: list[str]` — names created on YNAB.
  - `skipped: list[tuple[str, str]]` — `(entry_id, reason)`.
  - `failed: list[tuple[str, str]]` — `(name, error)`.
  The `TODO(plan-2)` markers and `print()` calls are removed.
- **A3:** Accounts & Merchants screens get a binding (proposed **`y`** =
  "push missing to YNAB", final key chosen in the plan) that runs the
  matching reconcile fn and shows a small results modal summarizing
  created/skipped/failed. `check_action` scopes the binding to those two
  screens. After creating records the user can `ctrl+r` to re-sync; this
  is not automatic.

### 4 · Cleanup (F2 + F3)

- **F2:** `load_all` parallelizes its seven fetches via
  `asyncio.gather(asyncio.to_thread(fn), …, return_exceptions=True)`;
  the first exception becomes `data.error`. The `LoadedData` shape is
  unchanged. (This is exactly what the loader's own docstring suggests.)
- **F3:** delete `tui/screens/placeholder.py`, drop its import
  (`app.py:18`) and the stale docstring line (`app.py:5`).

## Testing

Mirrors existing `tests/tui/test_*_picker.py` and engine-test patterns.

- **Picker modal:** selection returns the id; filter narrows the list;
  single-budget auto-select; cancel returns `None`.
- **Bootstrap:** `budget_id=None` → picker shown; valid id → load
  proceeds; stale id (not in fetched list) → picker shown.
- **Refresh:** guard fires when pending > 0; flush / discard / cancel
  branches behave correctly; reload re-binds all screens.
- **Reconcile:** `ReconcileResult` carries the right created/skipped/
  failed entries against a fake client; the screen binding surfaces them.
- **`load_all`:** returns the correct bundle; an injected fetch exception
  surfaces via `error`.

## Risks / notes

- Boot adds one always-on `get_plans()` call (needed for B1 and
  stale-budget validation). Cheap; acceptable.
- `FlushConfirmModal` likely needs a third "discard" action added (today
  it returns only `flush` / `cancel`).
- Per decision (3), reconcile writes on a single `y` keypress with no
  pre-confirm; the results modal is the feedback.

## Out of scope (other sub-projects)

- **Smarter memory (D1)** — auto-applying high-confidence merchant
  memory.
- **Sync power features (C1–C4)** — search/filter/jump, bulk categorize,
  inline memo/payee edit, delete-twin.
- **B2/B3** — sync date-window control, in-app credential repair.
