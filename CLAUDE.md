# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Finab is a CLI tool that syncs financial transactions from FinWise to YNAB (You Need A Budget). It handles account mapping, transaction deduplication, payee linking, transfer detection, and interactive categorization with per-merchant memory.

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

## Architecture

### Three-phase sync

The top-level `main()` (`src/finab/main.py`) runs three phases in order, all sharing one `ConfigStore`:

1. **`sync_accounts`** (`main.py`) — for each FinWise account not yet in the store, prompts for an alias and an `ignore_transactions` flag, then links to an existing YNAB account by name or creates a new one.
2. **`sync_merchants`** (`main.py`) — for each distinct FinWise merchant id seen in transactions, prompts for an alias; if the alias matches an account, links to that account's transfer payee (`_link_account_transfer_payee`); otherwise links to an existing YNAB payee or creates one.
3. **`sync_transactions`** (`transactions.py`) — fetches all FinWise + YNAB transactions, dedups via `merge_and_filter_transactions`, and walks each remaining candidate through `_process_one_transaction` (auto-paths or interactive prompt). Pushes are batched in a `_PendingQueue` and flushed on `f`, `q`, end-of-loop, or Ctrl+C.

### Module map (`src/finab/`)

- `main.py` — entrypoint, phases 1 & 2, top-level CLI prompts.
- `transactions.py` — phase 3 (`sync_transactions`), dedup (`merge_and_filter_transactions`), per-transaction dispatcher (`_process_one_transaction`), pending-queue (`_PendingQueue`), merchant memory write-back (`_update_merchant_memory`), and the `TransactionsStore` (owns `transactions.json`).
- `store.py` — `ConfigStore`, owns `config.json` (accounts, merchants, per-merchant memory). Also exposes `to_dict` and `normalize_alias` helpers.
- `config.py` — small load/save helpers for the top-level `budget_id`.
- `models.py` — Pydantic models. `Transaction` and `Account` are the unified internal models with `from_finwise` / `from_ynab` / `to_ynab` converters acting as an anti-corruption layer between the two APIs. `YNABTransaction` is a dataclass used to feed the YNAB SDK.
- `client.py` — `FinWiseClient` wrapping the `finwise-python` SDK.
- `ynab_client.py` — `YNABClient` wrapping the `ynab` SDK.

### State files

Two JSON files in the working directory:

- **`config.json`** — owned by `ConfigStore`. Keys:
  - `budget_id` (YNAB budget UUID)
  - `accounts` — `{internal_uuid: {alias, finwise, ynab, ignore_transactions}}`
  - `merchants` — `{internal_uuid: {alias, finwise, ynab, categories_used, processings}}`
    - `processings` is keyed by `str(amount_milliunits)` → `{parent_memo, splits}`. Drives the Enter-repeat and `r` (repeat-from-history) prompts during categorization.
- **`transactions.json`** — owned by `TransactionsStore`. A flat map `synced_transactions: {fw_uuid: ynab_import_id}` of every FinWise transaction we've pushed. Dedup uses this exclusively. Not ephemeral — deleting it forces every transaction to re-import. A recovery script exists at `scripts/recover_transactions_store.py` if it's ever lost or clobbered; it rebuilds the map from live FW + YNAB data by matching on `(account, date, amount)`.

### Dedup model

`merge_and_filter_transactions` (`transactions.py`):
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

### Phase 3 per-transaction dispatcher

`_process_one_transaction` (`transactions.py`) walks each candidate through:
- (a) `_is_inflow` — positive amount auto-categorized as "Inflow: Ready to Assign".
- (b) Resolve merchant from `txn.merchant_id`.
- (c) `_is_transfer(merchant)` — merchant linked to an account's transfer payee → auto-push as transfer (set `payee_id`, clear category).
- (c2) Diagnostic warning if FinWise marked the txn as a transfer but the merchant isn't a transfer payee.
- (d) No merchant → push with payee_name=memo, no category.
- (d2) `_is_before_current_month` → auto-push with payee but no category prompt (older transactions don't pull you into categorization).
- (e) Interactive prompt: `s`(plit) / `c`(ategory) / `r`(epeat from history) / `q`(uit, auto-flush) / `f`(lush now) / Enter (repeat closest-amount prior). The `r` and Enter paths apply via `_apply_processing_to_txn` which proportionally scales multi-split amounts to the current txn amount when amounts differ.

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

When adding integration tests that exercise `sync_transactions`, always pass an explicit `tx_store=TransactionsStore(tmp_path)` — the conftest sandbox is the backstop, not the contract.

## Environment Variables

Required in `.env`:
- `YNAB_ACCESS_TOKEN` — YNAB API personal access token
- `FINWISE_API_KEY` — FinWise API key (consumed directly by the `finwise-python` SDK)
