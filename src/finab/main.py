"""finab entrypoint.

The CLI was replaced by the Textual TUI in Plan 3. This module now
contains only the module-top imports, the re-export blocks for the
engine helpers (preserving existing import paths), and a tiny `main()`
that launches the TUI.
"""
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


def main():
    load_dotenv()
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
