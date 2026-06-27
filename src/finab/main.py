"""finab entrypoint.

The CLI was replaced by the Textual TUI in Plan 3. This module now
contains the module-top imports, the re-export blocks for the engine
helpers (preserving existing import paths), the `--reset` handler, and a
tiny `main()` that parses args and launches the TUI.
"""
import argparse
from pathlib import Path

from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.config import load_budget_id, save_budget_id
from finab.store import ConfigStore


# --- Re-exports from finab.engine.accounts ---
from finab.engine.accounts import (
    _calculate_starting_balance,
    _account_with_overrides,
    _reconcile_store_accounts_to_ynab,
)

# --- Re-exports from finab.engine.merchants ---
from finab.engine.merchants import (
    _link_account_transfer_payee,
    _extract_distinct_merchants,
    _reconcile_store_merchants_to_ynab,
    _record_merchant_alias,
)


def run_reset(input_fn=input, output_fn=print) -> bool:
    """Full reset: delete config.json and transactions.json behind a confirm
    prompt. Returns True iff files were deleted.

    Target paths are resolved at call time from the module constants
    `finab.store.CONFIG_FILE` and `finab.transactions.TRANSACTIONS_FILE` (not
    captured at import) so the test sandbox — which monkey-patches exactly
    those constants — protects this path too. `input_fn`/`output_fn` are
    injected so tests can drive the prompt without real stdin/stdout.
    """
    import finab.store as store_mod
    import finab.transactions as transactions_mod

    targets = [
        Path(store_mod.CONFIG_FILE),
        Path(store_mod.ACCOUNTS_FILE),
        Path(store_mod.MERCHANTS_FILE),
        Path(transactions_mod.TRANSACTIONS_FILE),
    ]
    existing = [p for p in targets if p.exists()]

    if not existing:
        output_fn("Nothing to reset — no state files found.")
        return False

    output_fn("This will permanently delete:")
    for p in existing:
        output_fn(f"  - {p}")
    answer = input_fn("Delete these files? [y/N] ").strip().lower()
    if answer not in ("y", "yes"):
        output_fn("Reset cancelled.")
        return False

    for p in existing:
        p.unlink()
        output_fn(f"Deleted {p}")
    output_fn("Reset complete.")
    return True


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="finab",
        description="Sync FinWise transactions into YNAB.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete config.json and transactions.json (full reset), then exit.",
    )
    args = parser.parse_args()

    if args.reset:
        run_reset()
        return

    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    FinabApp(
        fw_client=FinWiseClient(),
        ynab_client=YNABClient(),
        budget_id=load_budget_id(),
        store=ConfigStore(),
        tx_store=TransactionsStore(),
    ).run()


if __name__ == "__main__":
    main()
