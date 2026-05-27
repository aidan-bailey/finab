# Textual TUI for finab

**Date:** 2026-05-27
**Status:** Approved design — awaiting implementation plan
**Author:** Claude + user (brainstorming session)

## Goal

Replace the existing prompt-based CLI in `src/finab/main.py` and `src/finab/transactions.py` with a unified [Textual](https://pypi.org/project/textual/) TUI that covers all three sync phases (accounts, merchants, transactions) plus new browsing capabilities (per-merchant memory, account/merchant management) that the CLI doesn't expose today.

## Motivation

Today's CLI:

- Every interaction blocks on `input()`. Re-prints all visual state on each turn.
- Phase 3 (`_process_one_transaction` in `transactions.py`) is the bottleneck: rich prompts (`s` / `c` / `r` / `q` / `f` / Enter), category pickers nested two deep, no fuzzy search, no way to revisit a decision before flush.
- Per-merchant memory (`processings`) lives in `config.json` and can only be edited by hand.
- Accounts and merchants can only be re-linked by editing `config.json` by hand.

A TUI lets us keep visual state persistent (header, footer, queue), drive interactions with single keystrokes, expose data that the CLI hides, and allow editing decisions before they hit YNAB.

## Architecture

### Headless engine + Textual views

Two new subpackages, one-way import direction.

```
src/finab/
  client.py             (unchanged)
  ynab_client.py        (unchanged)
  models.py             (unchanged)
  config.py             (unchanged)
  store.py              (unchanged)
  engine/               (NEW — headless, no Textual, no I/O beyond stores/clients)
    sync.py             SyncEngine: drives phase 3 as a state machine
    accounts.py         AccountsEngine: pure functions over ConfigStore
    merchants.py        MerchantsEngine: pure functions over ConfigStore
    decisions.py        Decision dataclasses (Categorize, Split, ForceTransfer)
  tui/                  (NEW — Textual app, may import engine/)
    app.py              FinabApp(App): mounts screens, owns config + clients
    screens/
      sync.py
      accounts.py
      merchants.py
      memory.py
      settings.py
    widgets/
      transaction_card.py
      pending_list.py
      category_picker.py
      split_editor.py
    keymap.py
    styles.tcss
  main.py               replaced by entrypoint that calls tui.app.FinabApp().run()
  transactions.py       gutted: TransactionsStore stays; everything else moves to engine/
```

**Import direction**: `tui/*` may import `engine/*`, `store.py`, `models.py`. Nothing in `engine/*` may import `tui/*` or `textual.*`.

### Engine contract

`SyncEngine` constructor takes the fetched FW + YNAB data, a `ConfigStore`, a `TransactionsStore`. It exposes:

- `candidates: list[Candidate]` — every txn after dedup, each with `status` (`pending` / `auto` / `decided` / `flushed`), an optional auto-applied decision, and a slot for a user decision.
- `apply(candidate_id, decision)` — record a decision, update merchant memory via `ConfigStore.set_merchant_memory`. No YNAB call.
- `flush()` — push all `decided` + `auto` candidates via `YNABClient.create_transactions` / `update_transactions`. Marks them `flushed`. Raises on API failure (no swallowing).
- `undo(candidate_id)` — clear a user decision back to `pending`. Does *not* revert merchant memory (matches today's "last decision per amount wins" semantics).

No reactive observer pattern. Screens call engine methods, then ask Textual to re-render the affected widget.

## Sync screen

### Layout

```
┌─finab─────────┬─Sync ── phase 3 ── 12/47 ── 5 pending ──────────────────┐
│ ▶ Sync        │ ┌─Pending (47)─────────┐ ┌─Costco — 2026-05-22────────┐ │
│   Accounts    │ │ ✓ Amazon    -23.99   │ │ Amount: -84.21             │ │
│   Merchants   │ │ ✓ Amazon    -41.02   │ │ Memo:   COSTCO WHSE #1234  │ │
│   Memory      │ │ ✓ Chevron   -55.00   │ │ Status: pending            │ │
│   Settings    │ │ ▶ Costco    -84.21   │ │                            │ │
│               │ │ ○ Costco    -12.50   │ │ [↵] repeat → Groceries     │ │
│               │ │ → Self → Card -50.00 │ │     (last: -84.21)         │ │
│               │ │ + Salary  +1500.00   │ │                            │ │
│               │ └──────────────────────┘ └────────────────────────────┘ │
├────────────────┴──────────────────────────────────────────────────────────┤
│ j/k navigate · enter repeat · c category · s split · r history · f flush │
└──────────────────────────────────────────────────────────────────────────┘
```

### Status glyphs (pending list left column)

| Glyph | Meaning |
|---|---|
| `○` | undecided (pending) |
| `✓` | decided by user |
| `→` | auto-transfer |
| `+` | auto-inflow |
| `↷` | pre-current-month auto-push (no category) |
| `✗` | no-merchant auto-push (no category) |
| `⚠` | FW marks transfer but merchant not linked (manual fix needed) |
| `⇡` | flushed (already pushed to YNAB) |

Glyph drives the row's CSS class for colouring.

### Candidate state machine

```
                ┌──────────┐
load_candidates │ pending  │ ── user picks category/split/transfer ──▶ decided
   ──────────▶  │   (○)    │ ── auto-rule fires (inflow/transfer/…) ─▶ auto
                └──────────┘
   undo ◀──────────┘   ▲
                       │ flush()
                       ▼
                  ┌──────────┐
                  │ flushed  │       (terminal)
                  │   (⇡)    │
                  └──────────┘
```

`auto` and `decided` are flushed identically; the distinction exists only for the glyph and so `undo` knows whether the user actually chose.

### Keybindings on SyncScreen

| Key | Action |
|---|---|
| `j` / `k` / arrows | move cursor in pending list |
| `Enter` | repeat closest-amount processing (if any) |
| `c` | open category picker modal |
| `s` | open split editor modal |
| `r` | open repeat-from-history modal |
| `t` | force-mark as transfer (manual override) |
| `u` | undo decision on current row (`decided` / `auto` → `pending`) |
| `f` | flush (push all decided+auto to YNAB) |
| `q` / `Ctrl+C` | quit — confirm flush if pending |
| `g` / `G` | jump to top / bottom |
| `?` | help overlay |

### Category picker modal (`c`)

`ModalScreen` with input at top, ranked list below. Ranking: merchant's used categories first (with `(Nx)` frequency count), separator, then all other non-hidden non-deleted categories. Filter is substring-case-insensitive on `"{group_name}/{category_name}"`. `↑/↓` move, `Enter` picks, `Esc` cancels, `Ctrl+N` opens a sub-modal for create-new-category (reusing `YNABClient.create_category` / `create_category_group`).

```
┌─Pick category────────────────────────────────────────────┐
│  groc_                                                    │
├───────────────────────────────────────────────────────────┤
│ ▶ Groceries          (18x for Costco)                    │
│   Household groceries (Immediate Obligations)            │
│   Grocery delivery   (Quality of Life)                   │
│                                                           │
│ ⏎ select  ^N new  ^S split  esc cancel                   │
└───────────────────────────────────────────────────────────┘
```

### Split editor modal (`s`)

Live table. Starts with one row equal to the txn amount. `Tab` / `Enter` in the last cell appends another row with the remainder. Per-row: amount, category (opens picker), memo. "Remaining" line at the bottom: green when zero, red otherwise. `Ctrl+S` confirms (disabled until remaining = 0). `Esc` cancels.

```
┌─Split Costco — total: -84.21──────────────────────────────────────┐
│  #  Amount    Category               Memo                          │
│  1  -50.00    Groceries              produce                       │
│  2  -34.21    Household              cleaning supplies              │
│  +  add row                                                         │
│                                                                    │
│  Remaining: 0.00 ✓             Ctrl+S confirm   Esc cancel        │
└────────────────────────────────────────────────────────────────────┘
```

### Repeat-from-history modal (`r`)

`OptionList` over `merchant.processings`, "closest" entry pre-highlighted. Enter applies via `_apply_processing_to_txn` (moved into `engine/sync.py`).

## Other screens

### Accounts

List of every FW account from `FinWiseClient.get_accounts()` with state icon.

```
┌─Accounts (12)─────────────────────────────────────────────────────────┐
│ ✓  Chase Checking    →  Chase Checking          checking              │
│ ✓  Chase Savings     →  Emergency Fund          savings               │
│ ✓  Amex Gold         →  Amex Gold               creditCard            │
│ ⏸  Crypto Wallet     →  (ignored)                                     │
│ !  New BoA Card      →  (unlinked)              ← action needed       │
│ ✓  Mortgage          →  Home Mortgage           mortgage (tracking)   │
│                                                                       │
│ a alias · l link · n new ynab account · i toggle-ignore · r refresh   │
└───────────────────────────────────────────────────────────────────────┘
```

Status: `✓` linked, `⏸` ignored, `!` unlinked. Actions: rename alias, link to existing YNAB account, create new YNAB account, toggle ignore_transactions, refresh. New capabilities vs. CLI: rename alias, re-link to a different YNAB account.

### Merchants

```
┌─Merchants (138)───────────────────────────────────────────────────────┐
│  ✓  Costco Wholesa..  →  Costco                payee                  │
│  →  Self → Chase      →  Chase Checking        transfer payee         │
│  ✓  Amazon            →  Amazon                payee                  │
│  !  WEYRMOUNT FAR..   →  (unlinked)            ← action needed        │
│                                                                       │
│  /  filter · a alias · l link · u unlink · r refresh                  │
└───────────────────────────────────────────────────────────────────────┘
```

Two link variants: `✓` payee, `→` transfer payee. Link picker = fuzzy search over YNAB payees + the user's own accounts; typing an account name auto-routes to that account's transfer payee (matches `_link_account_transfer_payee` in current `main.py`).

### Memory

Browse and clean up per-merchant `processings`. New capability — CLI can't do this.

```
┌─Memory─────────────────────────────────────────────────────────────────┐
│ ▼ Costco (18 categorizations, 6 distinct amounts)                     │
│     -84.21  Groceries                                                  │
│     -41.02  Household                                                  │
│     -34.50  Groceries 25.00 + Household 9.50  (split)                 │
│     -12.50  Gas              ← delete to forget                       │
│   ▶ Amazon (44 categorizations, ...)                                   │
│   ▶ Netflix (12 categorizations, ...)                                  │
│                                                                       │
│  / filter · enter expand · d delete entry · r reset merchant memory   │
└────────────────────────────────────────────────────────────────────────┘
```

`d` deletes one processing entry. `r` clears the merchant's `processings` + `categories_used`. Writes through `ConfigStore.set_merchant_memory`.

### Settings

- **Budget**: current budget id, picker to switch (`YNABClient.get_budgets`); switching reloads state.
- **Credentials**: read-only status of `YNAB_ACCESS_TOKEN` and `FINWISE_API_KEY`. Reload `.env` action.
- **State files**: paths to `config.json` and `transactions.json`.

## Data flow & persistence

**Startup**: `FinabApp.on_mount` runs concurrent fetches via Textual workers — FW accounts/merchants/transactions, YNAB accounts/payees/transactions/categories/category_groups. Each screen populates as its data arrives. Sync screen waits for FW txns + YNAB txns + categories before constructing `SyncEngine` and running `merge_and_filter_transactions`.

**On `engine.apply()`**: mutate candidate state, write merchant memory via `ConfigStore.set_merchant_memory`, emit Textual message for row re-render. No YNAB call.

**On `engine.flush()`**: snapshot `creates` and `updates` from candidates, send each in batched API calls (same as today's `_PendingQueue.flush`), mark candidates `flushed` on success. Import-id mappings in `transactions.json` were already written at load time by `merge_and_filter_transactions` (matches today's behaviour: record-before-push, so a mid-push crash doesn't lose track of what was synced).

**Refresh**: per-screen `r` re-fetches that screen's data only — except on the Sync screen, where `r` is bound to repeat-from-history. The Sync screen has no soft refresh because re-running dedup discards in-flight decisions; that's only reachable via global `Ctrl+R`, which re-fetches *everything* and, on Sync, prompts a confirmation modal before discarding un-flushed decisions ("3 unflushed decisions will be lost. Continue? [y/N]").

## Error handling

**Fetch failures**: each worker catches exceptions, stores them on its screen's state, renders an error banner with `[retry]` action. Other screens stay usable.

**Flush failures**: `YNABClient.create_transactions` / `update_transactions` raise on non-2xx — we don't swallow. Exception propagates to `SyncScreen`, which shows a modal and leaves affected candidates in pre-flush state. Same retry-on-`f` semantics as today.

**Ctrl+C**: overridden on Sync screen to mirror today's behaviour — if pending decisions exist, show "Flush N pending before exit? [Y/n/cancel]" modal.

**Config corruption**: `ConfigStore._load` / `TransactionsStore._load` already tolerate malformed JSON (return `{}` on `JSONDecodeError`). No new handling.

**Inflow category missing**: candidate enters `pending` instead of `auto` with a tooltip explaining. Mirrors today's fall-through in `_process_one_transaction`.

**FW transfer-but-not-linked**: row gets `⚠` glyph; detail pane shows the same yellow warning text inline.

## Testing

**Engine** (`tests/engine/`): pure pytest. Construct `SyncEngine` with fixture FW + YNAB data, drive with `apply` / `undo` / `flush`, assert on candidate list + store side effects. Existing `tests/test_transactions.py::TestMergeAndFilter` migrates wholesale (signature stays).

**TUI** (`tests/tui/`): `app.run_test()` + `Pilot`. Two categories — smoke (screens mount without raising) and interaction (`pilot.press(...)` to drive widgets, assert engine state). We avoid Textual snapshot tests; brittle and review-unfriendly for our use case.

**Stubbed clients** (`tests/fakes/finwise.py`, `tests/fakes/ynab.py`): return preconfigured objects matching the real client data shapes. No network.

Existing `tests/conftest.py` sandbox (which re-points `TRANSACTIONS_FILE` and `CONFIG_FILE` to a tempdir) still applies — all engine tests benefit; new TUI tests should also pass explicit `tmp_path`-backed stores.

## Migration plan

The working tree stays green and `uv run finab` keeps working between every commit.

1. **Extract pure engine, no UI change.** Move `merge_and_filter_transactions`, `_find_inflow_category`, `_is_transfer`, `_is_inflow`, `_is_before_current_month`, `_apply_processing_to_txn`, `_closest_processing`, `_account_is_tracking`, `_TRACKING_ACCOUNT_TYPES`, `_INFLOW_CATEGORY_NAMES` into `engine/sync.py`. Re-export from `transactions.py` so existing imports / tests don't break. Run test suite.

2. **Build `SyncEngine` shell.** Class wrapping extracted functions with `load_candidates` / `apply` / `undo` / `flush`. Existing `_process_one_transaction` / `sync_transactions` stay alive in parallel — engine exists but isn't yet wired into the CLI. Add `tests/engine/test_sync.py`.

3. **Build accounts & merchants engines.** Extract logic from `main.py`'s `sync_accounts` / `sync_merchants` into `engine/accounts.py` / `engine/merchants.py`. Existing CLI keeps using them via re-exports.

4. **Add Textual scaffolding.** New `tui/app.py` with sidebar layout, empty placeholder screens. Reachable behind `FINAB_TUI=1` env flag — default still hits old CLI. Develop and dogfood without breaking existing entrypoint.

5. **Implement screens one at a time, behind the flag.** Sync first (highest value, hardest), then Accounts, Merchants, Memory, Settings. After each: `tests/tui/test_<screen>.py` + dogfood on a real sync run.

6. **Cutover.** Flip entrypoint: `uv run finab` runs the TUI. Add temporary `--classic` flag for the old prompt flow.

7. **Delete old code.** Remove `_process_one_transaction`, the interactive bodies of `sync_accounts` / `sync_merchants`, prompt helpers, `_PendingQueue`, ANSI color helpers. Remove `--classic` flag. Drop obsolete tests in `tests/test_main.py`. Final tree: no `input()` outside `tui/`.

Steps 1–4 are foundation; step 5 is the bulk of the work and is decomposable into per-screen sub-plans; step 7 is mechanical.

## Out of scope

- Mouse support beyond Textual's defaults. Keyboard-first design.
- Multi-budget UI beyond a budget picker in Settings.
- Editing already-flushed transactions (would require re-pushing as updates, plus a "this was already sent to YNAB" UX). Today's CLI can't do this either.
- Browser-rendered Textual via [textual-serve](https://github.com/Textualize/textual-serve). Local-only TTY.
- An undo log that survives app restart. `undo` only works on in-memory decisions before flush.
