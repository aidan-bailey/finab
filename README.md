# finab

A CLI tool that syncs financial transactions from [FinWise](https://finwise.app) to [YNAB](https://www.ynab.com/) (You Need A Budget). Finab handles account mapping, deduplication, payee aliasing, transfer detection, and rule-based categorization.

## Features

- **Account sync** — Maps FinWise accounts to existing YNAB accounts and creates any that are missing.
- **Transaction sync** — Pulls FinWise transactions, merges them with existing YNAB transactions, and either creates new ones or updates changed fields.
- **Deduplication** — Uses salted SHA-256 hashing of source IDs to produce stable YNAB `import_id`s, so re-runs are idempotent.
- **Transfer detection** — Automatically links transactions between two tracked accounts as YNAB transfers based on payee-to-account name matching.
- **Payee aliasing** — Resolves merchant IDs and regex patterns to clean, human-readable payee names.
- **Auto-categorization** — Applies regex rules to assign categories, with optional interactive confirmation for rules prefixed with `?`.

## Requirements

- Python ≥ 3.14
- [`uv`](https://github.com/astral-sh/uv) package manager
- A FinWise API key
- A YNAB personal access token

A Nix flake (`flake.nix`) is provided for reproducible dev shells that include Python 3.14, uv, and the necessary SSL certs.

## Installation

```bash
# Clone and enter the project
git clone <repo-url> finab
cd finab

# Install dependencies
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
FINWISE_API_KEY=your_finwise_key
YNAB_API_KEY=your_ynab_personal_access_token
```

### `config.json`

Finab persists its settings in `config.json` in the working directory. The file is created and updated automatically as you use the CLI, but you can edit it by hand. Supported keys:

| Key | Purpose |
| --- | --- |
| `budget_id` | YNAB budget UUID. Selected interactively on first run. |
| `account_aliases` | Maps FinWise account names → YNAB account names. |
| `merchant_aliases` | Maps FinWise merchant IDs → preferred YNAB payee names. |
| `payee_rules` | List of `{pattern, replacement}` regex rules applied to payee names. |
| `categories` | Map of regex → category name. Prefix the regex with `?` to require confirmation before applying. |
| `import_id_offset` | Salt used when hashing transaction IDs for YNAB `import_id`. Defaults to `finab_offset_v1`. **Changing this re-imports every transaction as new.** |

## Usage

Run the sync:

```bash
uv run finab
```

On first run, finab will:

1. Prompt you to select a YNAB budget (if more than one exists) and save the choice.
2. Sync accounts — listing FinWise accounts and creating any missing YNAB counterparts.
3. Pull FinWise transactions and reconcile them with YNAB:
   - Existing matches are updated in place when fields differ.
   - New transactions are created.
   - Transfers between tracked accounts are linked as YNAB transfer pairs.
   - Categories tagged with `?` prompt for confirmation in the terminal.

All amounts are displayed and stored in **milliunits** (1000 = $1.00), matching YNAB's native representation.

## Architecture

Finab is organized around a small set of modules under `src/finab/`:

- `main.py` — orchestrates the sync pipeline (`sync_accounts` → `sync_transactions`).
- `models.py` — unified `Transaction` and `Account` Pydantic models with `from_finwise` / `from_ynab` / `to_ynab` converters that act as an anti-corruption layer between the two APIs.
- `client.py` — `FinWiseClient` wrapping the `finwise-python` SDK.
- `ynab_client.py` — `YNABClient` wrapping the `ynab-api` SDK.
- `config.py` — load/save helpers for `config.json`.

The transaction pipeline is roughly:

```
fetch_transactions
        │
        ▼
merge_and_filter_transactions   ← hashed import_id matching, transfer exclusion
        │
        ▼
process_payee_aliases           ← merchant IDs + regex rules + transfer detection
        │
        ▼
process_categories              ← regex rules, optional confirmation
        │
        ▼
sync_changes_to_ynab            ← create new / patch existing
```

## Development

Run the test suite:

```bash
uv run pytest
```

Run a single test file or test:

```bash
uv run pytest tests/test_hashing.py
uv run pytest tests/test_hashing.py::TestHashingAndMatching::test_hashed_import_id_generation
```
