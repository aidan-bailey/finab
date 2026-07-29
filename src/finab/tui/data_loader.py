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
from datetime import date
from typing import Optional


def _initial_window_start(today: date) -> date:
    """First day of the month two months before `today`'s month.

    Bounds the initialisation fetch: the current month-to-date plus the two
    preceding full calendar months (today 2026-06-26 -> 2026-04-01, covering
    April + May in full and June so far). Aligns with the sync engine's
    'pre-month' rule — this month's txns auto-process while the two prior
    months surface as pre-month pending.
    """
    month = today.month - 2
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


@dataclass
class LoadedData:
    """Bundled result of all data fetches needed by the TUI on boot."""
    fw_accounts: list = field(default_factory=list)
    fw_transactions: list = field(default_factory=list)
    fw_merchants: dict = field(default_factory=dict)  # {merchant_id: name}
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
        # Initialisation window: only the last two months + current month-to-date.
        # FinWise (the source) is bounded; YNAB stays unbounded so dedup's
        # prune_stale sees the full live set and can't drop out-of-window mappings.
        data.fw_transactions = fw_client.get_transactions(
            start_date=_initial_window_start(date.today())
        )
        data.ynab_accounts = ynab_client.get_accounts(budget_id)
        data.ynab_transactions = ynab_client.get_transactions(budget_id)
        data.ynab_categories = ynab_client.get_categories(budget_id)
        data.ynab_category_groups = ynab_client.get_category_groups_with_categories(budget_id)
        data.ynab_payees = ynab_client.get_payees(budget_id)
    except Exception as e:
        data.error = e

    # Best-effort: backfill FinWise merchant names. The /transactions endpoint
    # omits them; /merchants supplies the {id: name} the UI shows. This is a
    # display nicety — a failure here must NOT fail the load, so it lives
    # outside (and after) the load-critical block above.
    get_merchants = getattr(fw_client, "get_merchants", None)
    if get_merchants is not None:
        try:
            name_map = get_merchants() or {}
            data.fw_merchants = name_map
            for txn in data.fw_transactions:
                mid = getattr(txn, "merchant_id", None)
                if mid and not getattr(txn, "merchant_name", None):
                    try:
                        txn.merchant_name = name_map.get(mid)
                    except (AttributeError, ValueError):
                        pass  # immutable/odd txn object — skip silently
        except Exception:
            pass  # names unavailable; transactions still sync fine

    return data
