"""Phase 3: transaction sync with interactive categorization.

This module owns the per-transaction prompt loop, the pending queue,
and the orchestration of fetch -> dedup -> categorize -> push.
"""
import sys
from datetime import date
from typing import Any, Optional

from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.store import ConfigStore, normalize_alias


# Names YNAB might use for the inflow category. Checked in this order.
_INFLOW_CATEGORY_NAMES = (
    "inflow: ready to assign",
    "ready to assign",
    "inflow: to be budgeted",
    "to be budgeted",
)


def _is_inflow(txn) -> bool:
    """A positive amount on a YNAB transaction is an inflow."""
    return getattr(txn, "amount", 0) > 0


def _is_transfer(merchant: Optional[dict]) -> bool:
    """A merchant whose YNAB record carries a transfer_account_id is a
    transfer payee — the transaction is a transfer to/from one of the
    user's own accounts."""
    if not merchant:
        return False
    return merchant.get("ynab", {}).get("transfer_account_id") is not None


def _find_inflow_category(categories) -> Optional[str]:
    """Find the YNAB category id for 'Inflow: Ready to Assign' (or its
    legacy variants). Returns the id of the first matching, non-hidden,
    non-deleted category; or None if none exists."""
    by_name = {}
    for c in categories:
        if getattr(c, "hidden", False) or getattr(c, "deleted", False):
            continue
        name = getattr(c, "name", "") or ""
        by_name[name.lower()] = c
    for candidate in _INFLOW_CATEGORY_NAMES:
        c = by_name.get(candidate)
        if c is not None:
            return c.id
    return None


def merge_and_filter_transactions(fw_transactions, ynab_transactions, store: ConfigStore) -> list:
    """Map FinWise accounts to YNAB account ids via the store, dedup against
    existing YNAB transactions by hashed import_id, and skip ones already
    categorized in YNAB. Returns the list of FinWise transactions needing
    processing. Each returned transaction has:
      - account_id rewritten to the YNAB account id
      - import_id rewritten to the hashed form
      - ynab_id set if a matching uncategorized YNAB transaction was found
        (so the caller knows to PATCH instead of POST)
    """
    from finab.main import generate_import_id  # local import to avoid cycle
    from finab.config import load_import_id_offset

    offset = load_import_id_offset()

    ynab_by_import_id = {}
    for txn in ynab_transactions:
        if getattr(txn, "import_id", None):
            ynab_by_import_id[txn.import_id] = txn

    out = []
    matched_ynab_ids = set()
    for fw_txn in fw_transactions:
        acc = store.account_by_finwise_id(fw_txn.account_id)
        if not acc:
            continue
        ynab_account_id = acc["ynab"].get("id")
        if not ynab_account_id:
            continue

        hashed_id = generate_import_id(fw_txn.import_id, offset)
        fw_txn.import_id = hashed_id

        ynab_match = ynab_by_import_id.get(hashed_id)
        if ynab_match and ynab_match.id not in matched_ynab_ids:
            matched_ynab_ids.add(ynab_match.id)
            if getattr(ynab_match, "deleted", False):
                continue
            if ynab_match.category_id:
                # Already categorized — preserve user's manual YNAB work.
                continue
            fw_txn.ynab_id = ynab_match.id
            fw_txn.category_id = None

        fw_txn.account_id = ynab_account_id
        out.append(fw_txn)
    return out


# --- Color helpers (mirror main.py; kept local to avoid cross-module imports). ---
def _color(code: str, s: str) -> str:
    if not sys.stdout.isatty():
        return s
    return f"\033[{code}m{s}\033[0m"

def _bold(s: str) -> str:   return _color("1", s)
def _dim(s: str) -> str:    return _color("2", s)
def _cyan(s: str) -> str:   return _color("36", s)
def _yellow(s: str) -> str: return _color("33", s)


def _pick_category(
    merchant: dict,
    ynab_categories: list,
    category_groups: list,
    ynab_client: YNABClient,
    budget_id: str,
) -> Optional[str]:
    """Show the per-merchant category picker. Returns the chosen YNAB
    category id, or None if the user backed out."""
    cats_used: dict = merchant.get("categories_used", {}) or {}
    # Build {category_id: category_object} for quick lookups, excluding
    # hidden/deleted.
    by_id = {
        c.id: c
        for c in ynab_categories
        if not getattr(c, "hidden", False) and not getattr(c, "deleted", False)
    }
    # Sort used categories by frequency descending.
    used_sorted = sorted(
        [(cid, cnt) for cid, cnt in cats_used.items() if cid in by_id],
        key=lambda kv: (-kv[1], by_id[kv[0]].name.lower()),
    )

    while True:
        print()
        print(f"  {_bold('Categories for')} '{merchant.get('alias', '?')}':")
        for i, (cid, cnt) in enumerate(used_sorted, start=1):
            c = by_id[cid]
            print(f"   {i}. {c.name} {_dim(f'(used {cnt}x)')}")
        print()
        print(f"   {_dim('o)')} Other category")
        print(f"   {_dim('n)')} New category")
        print(f"   {_dim('b)')} Back")
        print()

        raw = input(_cyan("  Pick: ")).strip().lower()

        if not raw:
            continue
        if raw == "b":
            return None
        if raw == "o":
            picked = _pick_category_from_full_list(category_groups)
            if picked:
                return picked
            continue
        if raw == "n":
            picked = _create_new_category(category_groups, ynab_client, budget_id)
            if picked:
                return picked
            continue
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(used_sorted):
                return used_sorted[n - 1][0]
            print(f"  Out of range (1..{len(used_sorted)})")
            continue
        print(f"  Unrecognized: {raw!r}")


def _pick_category_from_full_list(category_groups: list) -> Optional[str]:
    """Flat numbered picker over every active YNAB category, grouped by
    category group for readability. Returns the chosen category id, or
    None if the user backs out (empty input)."""
    # Flatten while preserving group order.
    flat = []
    for g in category_groups:
        for c in getattr(g, "categories", []) or []:
            if getattr(c, "hidden", False) or getattr(c, "deleted", False):
                continue
            flat.append((g, c))

    if not flat:
        print(_dim("  No categories available."))
        return None

    print()
    last_group_id = None
    for i, (g, c) in enumerate(flat, start=1):
        if g.id != last_group_id:
            print(f"  {_bold(g.name)}")
            last_group_id = g.id
        print(f"   {i:>3}. {c.name}")
    print()
    raw = input(_cyan("  Pick a number, Enter to go back: ")).strip()
    if not raw:
        return None
    if raw.isdigit():
        n = int(raw)
        if 1 <= n <= len(flat):
            return flat[n - 1][1].id
        print(f"  Out of range (1..{len(flat)})")
    return None


def _create_new_category(
    category_groups: list, ynab_client: YNABClient, budget_id: str
) -> Optional[str]:
    """Walk the user through creating a new category (with the option to
    also create a new group on the fly). Returns the new category's id, or
    None if cancelled.

    Side effect: appends the new category to the chosen group's `.categories`
    list (and the new group to `category_groups` if one was created), so
    later prompts in the same run see them without re-fetching from YNAB.
    """
    name = input(_cyan("  New category name (Enter to cancel): ")).strip()
    if not name:
        return None

    # Pick or create a group
    print()
    print(f"  {_bold('Target group:')}")
    for i, g in enumerate(category_groups, start=1):
        print(f"   {i:>3}. {g.name}")
    print(f"   {_dim('n)')} New group")
    print(f"   {_dim('b)')} Back")
    print()

    chosen_group = None
    while chosen_group is None:
        raw = input(_cyan("  Pick: ")).strip().lower()
        if not raw or raw == "b":
            return None
        if raw == "n":
            grp_name = input(_cyan("  New group name (Enter to cancel): ")).strip()
            if not grp_name:
                return None
            try:
                new_grp = ynab_client.create_category_group(budget_id, grp_name)
            except Exception as e:
                print(f"  Failed to create category group: {e}")
                return None
            if not hasattr(new_grp, "categories") or new_grp.categories is None:
                new_grp.categories = []
            category_groups.append(new_grp)
            chosen_group = new_grp
        elif raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(category_groups):
                chosen_group = category_groups[n - 1]
            else:
                print(f"  Out of range (1..{len(category_groups)})")
        else:
            print(f"  Unrecognized: {raw!r}")

    try:
        new_cat = ynab_client.create_category(budget_id, name, chosen_group.id)
    except Exception as e:
        print(f"  Failed to create category: {e}")
        return None

    if chosen_group.categories is None:
        chosen_group.categories = []
    chosen_group.categories.append(new_cat)
    return new_cat.id


def _prompt_memo(default: str = "") -> str:
    """Prompt for a memo. Press Enter to keep `default`. Strips whitespace."""
    if default:
        shown = f"  Memo (Enter to keep '{default}'): "
    else:
        shown = "  Memo (Enter for none): "
    raw = input(shown).strip()
    return raw if raw else default


class _PendingQueue:
    """Holds categorized-but-not-yet-pushed transactions. Flushed on demand
    via the `f` command, at end of run, or after Ctrl+C confirmation."""

    def __init__(self):
        self.creates: list = []
        self.updates: list = []

    def count(self) -> int:
        return len(self.creates) + len(self.updates)

    def add(self, txn) -> None:
        if getattr(txn, "ynab_id", None):
            self.updates.append(txn)
        else:
            self.creates.append(txn)

    def flush(self, ynab_client: YNABClient, budget_id: str) -> bool:
        """Push all pending transactions in two batched calls. Returns True
        if both succeed (queue clears). On any exception, returns False and
        keeps the queue for retry."""
        try:
            if self.creates:
                ynab_client.create_transactions(budget_id, self.creates)
            if self.updates:
                ynab_client.update_transactions(budget_id, self.updates)
            self.creates.clear()
            self.updates.clear()
            return True
        except Exception as e:
            print(f"Flush failed: {e}")
            return False


def sync_transactions(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
) -> None:
    """Phase 3 entry point. Stub for now; populated by later tasks."""
    raise NotImplementedError("sync_transactions wired in later tasks")
