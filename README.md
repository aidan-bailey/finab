# finab

A CLI tool that syncs financial transactions from [FinWise](https://finwise.app) to [YNAB](https://www.ynab.com/) (You Need A Budget). Finab handles account mapping, deduplication, payee linking, transfer detection, and interactive categorization with per-merchant memory.

## Features

- **Account sync** — Maps FinWise accounts to existing YNAB accounts by name and creates any that are missing. Supports flagging parent/aggregator accounts (e.g. Discovery Bank ZAR) as `ignore_transactions` so their duplicated transactions are filtered out.
- **Merchant sync** — Maps FinWise merchants to YNAB payees, or to another tracked account's transfer payee when the alias matches.
- **Transaction sync** — Pulls FinWise transactions, deduplicates against YNAB via a stable per-transaction UUID stored in `transactions.json`, and either creates new transactions or updates existing uncategorized ones.
- **Deduplication** — Each FinWise transaction is assigned a durable random UUID on first push. The UUID is stored in `transactions.json` and sent to YNAB as `import_id`, so re-runs are idempotent. If a previously-synced YNAB transaction is missing on the next fetch (user deleted it), the UUID is rotated automatically.
- **Transfer detection** — When a merchant alias matches one of your own YNAB accounts, transactions for that merchant are pushed as YNAB transfers. Also surfaces a warning when FinWise marks a transaction as a transfer but our merchant linkage doesn't reflect it, so you can fix the alias.
- **Interactive categorization** — For each new transaction, finab shows the merchant, amount, and memo, and prompts you to pick a category, split, repeat a prior categorization, or skip. Per-merchant memory remembers prior splits so repeated transactions categorize themselves with one keystroke.

## Requirements

- Python ≥ 3.14
- [`uv`](https://github.com/astral-sh/uv) package manager
- FinWise credentials (handled by the `finwise-python` SDK)
- A YNAB personal access token

A Nix flake (`flake.nix`) is provided for reproducible dev shells that include Python 3.14, uv, and the necessary SSL certs.

## Installation

```bash
git clone <repo-url> finab
cd finab
uv sync
```

If you use Nix:

```bash
nix develop
uv sync
```

## Configuration

### Environment variables

Copy `.env.example` to `.env` and fill in your credentials:

```bash
YNAB_ACCESS_TOKEN=your_ynab_personal_access_token
# FinWise credentials are handled by the finwise-python SDK
```

### State files

Finab persists state in two JSON files in the working directory. Both are created and updated automatically — you can edit by hand, but most fields are managed by the CLI.

**`config.json`** — long-lived state (accounts, merchants, per-merchant memory).

| Key | Purpose |
| --- | --- |
| `budget_id` | YNAB budget UUID. Selected interactively on first run. |
| `accounts` | Map of internal-uuid → `{alias, finwise: {...}, ynab: {...}, ignore_transactions}`. Built up incrementally during account sync. |
| `merchants` | Map of internal-uuid → `{alias, finwise: {...}, ynab: {...}, categories_used, processings}`. `processings` is keyed by transaction amount and stores prior category assignments for the repeat-from-history feature. |

**`transactions.json`** — dedup state. A flat map `{fw_uuid: ynab_import_id}` of every FinWise transaction we've pushed. Not ephemeral: deleting it forces every transaction to re-import.

## Usage

Run the sync:

```bash
uv run finab
```

On first run, finab will:

1. Prompt you to select a YNAB budget (if more than one exists) and save the choice.
2. **Phase 1 — Account sync.** For each FinWise account not yet linked, ask for the YNAB account name (defaults to the FinWise name) and whether to ignore its transactions. Links to an existing YNAB account by name match, otherwise creates a new one.
3. **Phase 2 — Merchant sync.** For each distinct FinWise merchant not yet linked, ask for an alias. If the alias matches an account, link to that account's transfer payee. Otherwise, link to an existing YNAB payee by name or create one.
4. **Phase 3 — Transaction sync.** Fetch FinWise and YNAB transactions, dedup via `transactions.json`, and walk through each transaction needing categorization:
   - Positive amounts → auto-categorized as "Inflow: Ready to Assign".
   - Merchants linked to one of your accounts → auto-pushed as transfers.
   - Transactions before the current month → auto-pushed with the payee but no category.
   - Everything else → interactive prompt with options to pick a category, split into multiple categories, repeat the closest prior categorization (Enter), pick any prior categorization from history (`r`), quit (`q`), or flush pending pushes to YNAB (`f`).

All amounts are displayed and stored in **milliunits** (1000 = $1.00), matching YNAB's native representation.

## Architecture

Finab is organized under `src/finab/`:

- `main.py` — entrypoint and phases 1 & 2 (`sync_accounts`, `sync_merchants`).
- `transactions.py` — phase 3 (`sync_transactions`), the per-transaction interactive flow, dedup, and the `TransactionsStore`.
- `store.py` — `ConfigStore`, owns `config.json` (accounts, merchants, per-merchant memory).
- `config.py` — small helpers for reading/writing the top-level `budget_id`.
- `models.py` — unified `Transaction` and `Account` Pydantic models with `from_finwise` / `from_ynab` / `to_ynab` converters that act as an anti-corruption layer between the two APIs.
- `client.py` — `FinWiseClient` wrapping the `finwise-python` SDK.
- `ynab_client.py` — `YNABClient` wrapping the `ynab` SDK.

The phase-3 pipeline:

```
fetch FinWise + YNAB transactions
        │
        ▼
merge_and_filter_transactions   ← UUID dedup via transactions.json,
        │                          rotate-on-missing for deleted YNAB twins,
        │                          drop ignore_transactions accounts
        ▼
_process_one_transaction        ← per-transaction dispatcher: auto-inflow,
        │                          auto-transfer, pre-month auto-push,
        │                          no-merchant push, or interactive prompt
        ▼
_PendingQueue                   ← batches creates and updates; flushes on
        │                          'f', 'q', completion, or Ctrl+C
        ▼
ynab_client.create/update_transactions
```

## Development

Run the test suite:

```bash
uv run pytest
```

Run a single test file or test:

```bash
uv run pytest tests/test_transactions.py
uv run pytest tests/test_transactions.py::TestProcessOneTransaction::test_enter_replays_last_processing_when_amount_matches
```
