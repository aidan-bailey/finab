"""SettingsScreen — sidebar entry #5.

Read-only display of:
  - Current budget id
  - Credential status (presence of YNAB_ACCESS_TOKEN, FINWISE_API_KEY)
  - Paths to config.json and transactions.json

Interactive budget switching and .env reload deferred to Plan 4.
"""
import os
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class SettingsScreen(Container):
    """Sidebar entry #5 — settings + diagnostics."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._budget_id: Optional[str] = None

    def bind_data(self, *, budget_id: Optional[str] = None) -> None:
        self._budget_id = budget_id
        self._update_content()

    def compose(self) -> ComposeResult:
        yield Static("Settings", id="settings-title")
        yield Static("", id="settings-budget")
        yield Static("", id="settings-creds")
        yield Static("", id="settings-paths")

    def on_mount(self) -> None:
        self._update_content()

    def _update_content(self) -> None:
        try:
            self.query_one("#settings-budget", Static).update(
                f"  Budget ID:  {self._budget_id or '(not set)'}"
            )
        except Exception:
            return  # not mounted yet

        ynab = "set" if os.environ.get("YNAB_ACCESS_TOKEN") else "MISSING"
        fw = "set" if os.environ.get("FINWISE_API_KEY") else "MISSING"
        self.query_one("#settings-creds", Static).update(
            f"  Credentials:\n"
            f"    YNAB_ACCESS_TOKEN: {ynab}\n"
            f"    FINWISE_API_KEY:   {fw}"
        )

        # Read paths from the module-level constants in store/transactions.
        # Use module-level access so the test conftest sandbox is honored.
        import finab.store as store_mod
        import finab.transactions as txn_mod
        self.query_one("#settings-paths", Static).update(
            f"  State files:\n"
            f"    config.json:        {Path(store_mod.CONFIG_FILE).resolve()}\n"
            f"    transactions.json:  {Path(txn_mod.TRANSACTIONS_FILE).resolve()}"
        )
