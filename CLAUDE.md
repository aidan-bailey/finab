# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Finab is a terminal app (Textual TUI) that syncs financial transactions from FinWise into YNAB (You Need A Budget). It handles account mapping, transaction deduplication, payee linking, transfer detection, and interactive categorization with per-merchant memory.

## Commands

```bash
# Install dependencies (uses uv package manager)
uv sync

# Run the application
uv run finab

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_transactions.py

# Run a specific test
uv run pytest tests/test_transactions.py::TestMergeAndFilter::test_skips_already_categorized_match
```

## Domain Model (YNAB / FinWise / Finab semantics)

Finab bridges two systems with different vocabularies. `models.py` is the
anti-corruption layer that translates between them; this is the conceptual map
it implements.

**FinWise** — the *source* (read-only). A bank/card aggregator.
- **Account** — a real-world bank/card/loan account. Has `type` + `sub_type`
  (`depository/checking`, `credit`, `loan`, `investment`, …).
- **Merchant** — who you paid. Lives *on transactions only* — FinWise has no
  merchant endpoint, so the distinct-merchant list is derived by walking
  transactions (`_extract_distinct_merchants`). Keyed by `merchant_id`;
  `merchant_name` is often null.
- **Transaction** — `description` (human label), `amount` (signed **decimal**
  with `currency_code`, default ZAR), `date`, `merchant_id`, plus flags
  `is_transfer`, `is_pending`, `is_manual`, `needs_review`. FinWise's own
  `transaction_category_id` is **ignored** — Finab never maps FinWise categories.

**YNAB** ("You Need A Budget") — the *destination* (read/write).
- **Budget** — the top-level container (`budget_id`). ⚠️ The current SDK renamed
  this to **"plan"**: use `PlansApi.get_plans()` (`BudgetsApi` was removed).
- **Account** — either *on-budget* (`checking/savings/cash/creditCard`) or
  *tracking / off-budget* (`otherAsset/otherLiability/mortgage/…`). Tracking
  accounts take no categories (see `_TRACKING_ACCOUNT_TYPES`).
- **Payee** — who a transaction is paid to. Every account also gets an
  auto-created **transfer payee** ("Transfer: <account>") so moves between your
  own accounts net out instead of looking like spending.
- **Category** / **Category Group** — categories are nested under groups. The
  special **"Inflow: Ready to Assign"** category (legacy names handled too —
  see `_INFLOW_CATEGORY_NAMES`) receives inflows.
- **Transaction** — `amount` in **milliunits** (1000 = 1.00), `category_id`,
  `payee_id`/`payee_name`, `memo`, `import_id` (dedup key), `cleared`
  (`cleared`/`uncleared`/`reconciled`), `approved`, and optional
  `subtransactions` (a **split**). ⚠️ The SDK exposes the date as **`var_date`**,
  not `date`.

**The mapping (FinWise → YNAB), in `models.py`:**

| FinWise | → | YNAB | Notes |
|---|---|---|---|
| account `type`/`sub_type` | → | account `type` | `depository`→`checking`/`savings`; `credit`→`creditCard`; `loan`→`otherLiability`; `investment`→`otherAsset`; default `otherAsset` |
| `merchant_id` | → | payee, **or** transfer payee | a merchant whose alias matches one of your accounts links to that account's transfer payee |
| transaction `id` | → | `import_id` | FinWise `id` is the stable identity; the YNAB `import_id` is a UUID Finab mints and stores in `transactions.json` (see *Import IDs*) |
| `description` | → | `payee_name` **and** `memo` | same string into both |
| decimal `amount` | → | milliunits | `int(amount * 1000)`; sign preserved — **positive = inflow** |
| *(none)* | → | `category_id` | never mapped; assigned interactively in YNAB terms |

**Transfers — two easily-confused ids:**
- An **account** carries `transfer_payee_id` — the id of *its own* transfer
  payee. Used to **link** a merchant to it (`_link_account_transfer_payee`).
- A **payee**, when it is a transfer payee, carries `transfer_account_id` — the
  account it moves money to/from. Used to **detect** a transfer (`_is_transfer`).
- So: **link via `transfer_payee_id`, detect via `transfer_account_id`.**

## Architecture

### App shell + the three screens

`main()` (`src/finab/main.py`) is a thin launcher: it builds the clients + stores and runs the Textual app (`FinabApp`, `tui/app.py`). On mount, one background fetch (`load_all`, `tui/data_loader.py`) pulls everything — FinWise accounts + transactions and YNAB accounts, transactions, categories, category-groups, payees — into a `LoadedData` bundle and binds it to the screens. All screens share one `ConfigStore`.

The three original phases are now three sidebar **screens** the user navigates (no longer a forced sequence). Each screen's non-interactive logic lives in `engine/`:

1. **Accounts** (`tui/screens/accounts.py` + `engine/accounts.py`) — map each FinWise account to a YNAB account (link by name or create), set the `ignore_transactions` flag, rename/relink.
2. **Merchants** (`tui/screens/merchants.py` + `engine/merchants.py`) — map each distinct FinWise merchant to a YNAB payee. If the alias matches an account, link to that account's transfer payee (`_link_account_transfer_payee`); otherwise link an existing payee or create one.
3. **Sync** (`tui/screens/sync.py` + `engine/sync.py`) — dedup via `merge_and_filter_transactions`, build a `SyncEngine` of `Candidate`s, auto-apply the safe rules, and let the user categorize the rest. Decisions are batched in the engine and pushed on **flush** (`f`) or on quit (with a confirm prompt).

### Module map (`src/finab/`)

The non-interactive **engine** (`engine/`) holds all logic with no I/O; the **TUI** (`tui/`) is a thin Textual view over it. `main.py` and `transactions.py` re-export the engine helpers so old import paths keep working.

- `main.py` — entrypoint; builds clients/stores and launches `FinabApp`. Re-exports the `engine.accounts` / `engine.merchants` helpers.
- `engine/accounts.py` — phase-1 account logic (`_account_with_overrides`, `_calculate_starting_balance`, `_reconcile_store_accounts_to_ynab`).
- `engine/merchants.py` — phase-2 merchant logic (`_link_account_transfer_payee`, `_extract_distinct_merchants`, `_record_merchant_alias`, `_reconcile_store_merchants_to_ynab`).
- `engine/sync.py` — phase-3 logic: dedup (`merge_and_filter_transactions`), the auto-rules + state machine (`SyncEngine`, `Candidate`), merchant-memory write-back (`_update_merchant_memory`), and the repeat/scale helpers (`_apply_processing_to_txn`, `_closest_processing`). No network except `SyncEngine.flush`.
- `transactions.py` — owns the `TransactionsStore` (`transactions.json`); re-exports the `engine.sync` helpers above.
- `store.py` — `ConfigStore`, owns `config.json` (accounts, merchants, per-merchant memory). Also exposes `to_dict` and `normalize_alias` helpers.
- `config.py` — small load/save helpers for the top-level `budget_id`.
- `models.py` — Pydantic models. `Transaction` and `Account` are the unified internal models with `from_finwise` / `from_ynab` / `to_ynab` converters acting as an anti-corruption layer between the two APIs (see *Domain Model*). `YNABTransaction` is a dataclass used to feed the YNAB SDK.
- `client.py` — `FinWiseClient` wrapping the `finwise-python` SDK.
- `ynab_client.py` — `YNABClient` wrapping the `ynab` SDK.
- `tui/app.py` — `FinabApp`: sidebar + `ContentSwitcher` of 5 screens (Sync, Accounts, Merchants, Memory, Settings); owns the global key BINDINGS, scoped per active screen via `check_action`.
- `tui/data_loader.py` — `load_all` → `LoadedData` (the single upfront fetch).
- `tui/screens/`, `tui/widgets/` — per-screen containers and the modal pickers/cards (category picker, split editor, history picker, pending list, transaction card, …).

### State files

Two JSON files in the working directory:

- **`config.json`** — owned by `ConfigStore`. Keys:
  - `budget_id` (YNAB budget UUID)
  - `accounts` — `{internal_uuid: {alias, finwise, ynab, ignore_transactions}}`
  - `merchants` — `{internal_uuid: {alias, finwise, ynab, categories_used, processings}}`
    - `processings` is keyed by `str(amount_milliunits)` → `{parent_memo, splits}`. Drives the Enter-repeat and `r` (repeat-from-history) prompts during categorization.
- **`transactions.json`** — owned by `TransactionsStore`. A flat map `synced_transactions: {fw_uuid: ynab_import_id}` of every FinWise transaction we've pushed. Dedup uses this exclusively. Not ephemeral — deleting it forces every transaction to re-import. A recovery script exists at `scripts/recover_transactions_store.py` if it's ever lost or clobbered; it rebuilds the map from live FW + YNAB data by matching on `(account, date, amount)`.

### Dedup model

`merge_and_filter_transactions` (`engine/sync.py`, re-exported via `transactions.py`):
1. Build `ynab_by_import_id` from the live YNAB fetch (skip deleted).
2. `tx_store.prune_stale(...)` drops mappings whose import_id no longer exists on YNAB.
3. For each FW txn:
   - Skip if account unknown or has `ignore_transactions=True`.
   - Look up our durable `import_id` in `tx_store` via the FW UUID.
   - If found AND present in YNAB:
     - Skip if the YNAB twin is **already resolved** — `category_id` set, non-deleted subtransactions, `transfer_account_id` set, OR the account is a tracking type (see `_TRACKING_ACCOUNT_TYPES`).
     - Otherwise mark for UPDATE (set `ynab_id`).
   - If not found in YNAB → rotate (fresh UUID, push as new). Handles user-side deletes.
   - If never synced → assign a fresh UUID, push as new.

### Phase 3: SyncEngine + Sync screen

`SyncEngine` (`engine/sync.py`) is a pure state machine built from the dedup result. Construction sorts candidates (by merchant alias, then date) and runs the auto-rule pass (`_build_candidate`) over each `Candidate`. Every candidate has a `status` (`pending` → `decided`/`auto` → `flushed`) and an optional `auto_reason`.

Auto-rule pass, in priority order:
- (a) **inflow** — positive amount *and* an inflow category exists → `status=auto`, category set to "Inflow: Ready to Assign".
- (b) **transfer** — merchant linked to an account's transfer payee (`_is_transfer`) → `status=auto`, `payee_id` set, category cleared.
- (b2) Non-blocking **warning** if FinWise marked the txn `is_transfer` but the merchant isn't a transfer payee (it still flows through to the paths below).
- (c) **no-merchant** → `status=pending`, `auto_reason="no-merchant"`. *Not* auto-pushed — `flush` skips it until the user maps a merchant (`m`).
- (d) **pre-month** (dated before the 1st of the current month) → `status=pending`, `auto_reason="pre-month"`. Payee is set but it won't push without a category.
- Otherwise → `status=pending`: payee set from the merchant, user must categorize.

Only `decided` + `auto` candidates push; `flush()` splits them into create (no `ynab_id`) and update (has `ynab_id`) batches, marking each batch `flushed` only after its API call succeeds.

The **Sync screen** (`tui/screens/sync.py`) is a master/detail view over the engine. Keys (scoped to this screen by `FinabApp.check_action`): `c` category · `s` split · `r` repeat-from-history · `Enter` repeat closest-amount entry · `t` force-transfer · `m` map merchant (no-merchant candidates) · `u` undo a decision · `f` flush · `g`/`G` top/bottom · `q` quit (prompts to flush pending decisions). The `r`/`Enter` paths apply via `_apply_processing_to_txn`, which proportionally scales multi-split amounts when the current txn amount differs from the stored one.

## Important Implementation Details

- **Amounts in milliunits** (1000 = $1.00) throughout. Matches YNAB's native representation. Be careful: FinWise reports decimal amounts; `from_finwise` does `int(amount * 1000)`.
- **Import IDs are durable UUIDs**, not hashes. Stored in `transactions.json`. Sent to YNAB as `import_id` on create. On a missed YNAB twin we rotate (regenerate) rather than rely on the old id (which YNAB remembers and would silently dedupe a re-post against, even for deleted transactions).
- **YNAB payee names** are truncated to 50 chars at create time.
- **`subtransactions: []` on create must become `None`** before reaching the wire — YNAB treats an empty list as a 0-split split transaction and drops the parent `category_id`. `Transaction.to_ynab` already handles this.
- **PATCH model**: `update_transactions` builds `SaveTransactionWithIdOrImportId` objects. `ExistingTransaction` no longer satisfies `PatchTransactionsWrapper`'s strict type check.
- **Tracking accounts** (`_TRACKING_ACCOUNT_TYPES`: `otherAsset, otherLiability, mortgage, autoLoan, studentLoan, personalLoan, medicalDebt, otherDebt`) don't use categories. The dedup treats their twins as already-resolved even when `category_id=None`.
- **YNAB API account limits**: `POST /accounts` only accepts `SaveAccountType` (`checking, savings, cash, creditCard, otherAsset, otherLiability`). The richer types (`autoLoan` etc.) must be set in the YNAB UI; `refresh_records` will pick them up on the next sync. There is no PATCH endpoint for accounts.
- **YNAB UUID vs str**: the SDK returns `id`/`category_id`/`transfer_payee_id` as `uuid.UUID` instances, but our stored config uses strings. Always compare via `str(...)`. `_category_name` enforces this.
- **YNAB sidebar quirk**: API-created accounts appear under the "Cash" sidebar group in YNAB regardless of `type`. The `type` is preserved (visible in Edit Account) but the sidebar grouping is driven by linked-vs-unlinked. We print a warning after `create_account` telling the user to fix the type via the UI.

## Test Sandboxing

`tests/conftest.py` has an autouse session-scoped fixture that re-points `finab.transactions.TRANSACTIONS_FILE` and `finab.store.CONFIG_FILE` to a session tempdir. This protects against tests that accidentally instantiate `TransactionsStore()` / `ConfigStore()` with default paths — without the fixture, they'd read and overwrite the developer's real state files (pytest runs with cwd=repo root). The store classes themselves resolve their default paths lazily at `__init__` time so the monkey-patching takes effect.

When adding integration tests that exercise `merge_and_filter_transactions` or `SyncEngine`, always pass an explicit `tx_store=TransactionsStore(tmp_path)` — the conftest sandbox is the backstop, not the contract.

## Environment Variables

Required in `.env`:
- `YNAB_ACCESS_TOKEN` — YNAB API personal access token
- `FINWISE_API_KEY` — FinWise API key (consumed directly by the `finwise-python` SDK)
