"""Async data loader for the TUI.

`load_all` calls the seven data-fetch methods sequentially inside an
async function. Sequential, not parallel — the SDK clients are sync
(httpx-backed under the hood), and parallelizing inside one Textual
worker would require wrapping each call in run_in_executor, which is
more ceremony than it's worth for this volume of work. If load time
becomes a concern, parallelize with asyncio.to_thread per call.

All exceptions are caught and surfaced via LoadedData.error so the
TUI can show a banner instead of crashing.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LoadedData:
    """Bundled result of all data fetches needed by the TUI on boot."""
    fw_accounts: list = field(default_factory=list)
    fw_transactions: list = field(default_factory=list)
    ynab_accounts: list = field(default_factory=list)
    ynab_transactions: list = field(default_factory=list)
    ynab_categories: list = field(default_factory=list)
    ynab_category_groups: list = field(default_factory=list)
    ynab_payees: list = field(default_factory=list)
    error: Optional[Exception] = None


async def load_all(*, fw_client, ynab_client, budget_id: str) -> LoadedData:
    """Fetch everything the TUI needs on boot. Returns LoadedData.

    On any exception, returns LoadedData with `error` populated and
    partial data — callers can still render an error banner over
    whichever screens did get data.
    """
    data = LoadedData()
    try:
        data.fw_accounts = fw_client.get_accounts()
        data.fw_transactions = fw_client.get_transactions()
        data.ynab_accounts = ynab_client.get_accounts(budget_id)
        data.ynab_transactions = ynab_client.get_transactions(budget_id)
        data.ynab_categories = ynab_client.get_categories(budget_id)
        data.ynab_category_groups = ynab_client.get_category_groups_with_categories(budget_id)
        data.ynab_payees = ynab_client.get_payees(budget_id)
    except Exception as e:
        data.error = e
    return data
