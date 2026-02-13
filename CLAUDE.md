# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Finab is a CLI tool that syncs financial transactions from FinWise to YNAB (You Need A Budget). It handles account mapping, transaction deduplication, payee aliasing, and automatic categorization.

## Commands

```bash
# Install dependencies (uses uv package manager)
uv sync

# Run the application
uv run finab

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_hashing.py

# Run a specific test
uv run pytest tests/test_hashing.py::TestHashingAndMatching::test_hashed_import_id_generation
```

## Architecture

### Data Flow

1. **main.py** orchestrates the sync process:
   - `sync_accounts()` - Maps FinWise accounts to YNAB accounts, creates missing ones
   - `sync_transactions()` - Main sync flow: fetches, merges, aliases, categorizes, and syncs

2. **Transaction Processing Pipeline** (in `main.py`):
   - `map_accounts()` - Builds FinWise ID → YNAB ID mapping
   - `fetch_transactions()` - Gets transactions from both services
   - `merge_and_filter_transactions()` - Matches transactions by hashed `import_id`, excludes transfers
   - `process_payee_aliases()` - Applies merchant ID and regex-based payee rules
   - `process_categories()` - Applies category rules with optional confirmation prompts
   - `sync_changes_to_ynab()` - Creates new or updates existing transactions

### Key Models (models.py)

- `Transaction` - Unified model used internally, converts to/from FinWise and YNAB formats
- `Account` - Unified account model with type mapping between services
- `FinWiseTransaction` / `FinWiseAccount` - Pydantic models for FinWise API responses

### Client Wrappers

- `FinWiseClient` (client.py) - Wraps the `finwise-python` SDK
- `YNABClient` (ynab_client.py) - Wraps the `ynab-api` SDK

### Configuration (config.py)

All configuration is stored in `config.json`:
- `account_aliases` - Maps FinWise account names to YNAB account names
- `merchant_aliases` - Maps merchant IDs to payee names
- `payee_rules` - Regex patterns for payee name matching
- `categories` - Regex patterns for auto-categorization (prefix with `?` for confirmation)
- `budget_id` - Selected YNAB budget
- `import_id_offset` - Used for hashing transaction import IDs (defaults to `finab_offset_v1`)

## Environment Variables

Required in `.env`:
- `YNAB_ACCESS_TOKEN` - YNAB API personal access token
- FinWise credentials (handled by finwise-python SDK)

## Important Implementation Details

- Amounts are in milliunits (1000 = $1.00) throughout
- Import IDs are hashed with SHA-256 using a configurable salt for deduplication
- YNAB payee names are truncated to 50 characters
- Category rules prefixed with `?` require user confirmation before applying
