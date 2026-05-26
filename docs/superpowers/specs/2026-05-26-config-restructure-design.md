# Config Restructure: Accounts & Merchants as First-Class Entities

**Status:** Approved (design phase)
**Date:** 2026-05-26
**Scope:** `config.json` schema, Phase 1 (Account Sync), Phase 2 (Merchant-Payee Sync), YNAB SDK migration.
**Out of scope:** Phase 3 (transaction sync, categorization, splits, transfers). The existing `payee_rules`, `categories`, `budget_id`, and `import_id_offset` keys are preserved as-is.

## Motivation

Today's `config.json` stores aliases as flat dicts keyed by name (`account_aliases`) or by FinWise merchant ID (`merchant_aliases`). Three problems drive the restructure:

1. **Many-to-one merchants.** Multiple FinWise `merchant_id`s legitimately represent one real-world merchant. The live config already has the impedance: two IDs for "Easy Equities," three for "Lifestyle on Kloof," two for "FNB Aspire Credit Account." A flat `{fw_id: name}` dict cannot express "these three IDs are the same merchant."
2. **Canonical synced state.** `config.json` should mirror the full FinWise and YNAB records for each linked entity. The file becomes a ledger of synced state, not a thin link table.
3. **Reduce interactive prompts.** Merchant resolution moves out of the per-transaction loop and into a dedicated Phase 2 that iterates **distinct merchants**, so the user is prompted at most once per real merchant — not once per merchant occurrence.

Renames on either side are *not* a primary motivation; the design tolerates them (records get refreshed each run) but doesn't optimize for them.

## Schema

`config.json` after the restructure. Existing keys (`budget_id`, `payee_rules`, `categories`, `import_id_offset`) are unchanged:

```json
{
  "budget_id": "006fc4ef-...",
  "import_id_offset": "finab_offset_v1",
  "payee_rules": [...],
  "categories": {...},

  "accounts": {
    "<internal_uuid>": {
      "id": "<internal_uuid>",
      "alias": "Discovery Bank Transaction Account",
      "finwise": { /* full FinWiseAccount record */ },
      "ynab":    { /* full YNAB Account record */ }
    }
  },

  "merchants": {
    "<internal_uuid>": {
      "id": "<internal_uuid>",
      "alias": "Easy Equities",
      "finwise": {
        "<finwise_merchant_id_A>": { /* full FinWise merchant record */ },
        "<finwise_merchant_id_B>": { /* full FinWise merchant record */ }
      },
      "ynab": { /* full YNAB Payee record */ }
    }
  }
}
```

### Schema decisions

- **Internal IDs are UUID4.** Freshly generated when an entity is first created. Opaque: never displayed, never derived from anything mutable (alias, FinWise id, YNAB id). Survives renames and YNAB-side recreates.
- **Accounts are 1:1 on the FinWise side**, merchants are 1:many. `accounts[x].finwise` is a single record; `merchants[x].finwise` is a dict keyed by FinWise merchant ID.
- **Full records on both sides.** The file mirrors source-of-truth state. Drift is handled by overwriting cached records on every sync (see `ConfigStore.refresh_records`).
- **No persisted link tables.** Secondary indexes (FinWise ID → internal ID, alias → merchant ID) are built in memory on load. With ~10 accounts and ~50 merchants, rebuild cost is negligible and drift is structurally impossible.
- **Migration: fresh start.** Legacy `account_aliases` and `merchant_aliases` keys are removed. Phase 1 and Phase 2 rebuild from prompts on the next run.

## `ConfigStore` (`src/finab/store.py`, new)

Owns the new schema. All reads go through O(1) in-memory indexes; all writes touch only the primary store and trigger an index rebuild plus an atomic save.

```python
class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = path
        self._data: dict = _load(path)
        self._rebuild_indexes()

    # --- Indexes (rebuilt on load and after each write) ---
    def _rebuild_indexes(self) -> None:
        self._fw_account_index: dict[str, str] = {}     # fw_account_id -> internal_id
        self._fw_merchant_index: dict[str, str] = {}    # fw_merchant_id -> internal_id
        self._alias_merchant_index: dict[str, str] = {} # normalized alias -> internal_id

        for acc in self._data.get("accounts", {}).values():
            self._fw_account_index[acc["finwise"]["id"]] = acc["id"]

        for m in self._data.get("merchants", {}).values():
            for fw_id in m["finwise"]:
                self._fw_merchant_index[fw_id] = m["id"]
            self._alias_merchant_index[_normalize_alias(m["alias"])] = m["id"]

    # --- Reads ---
    def account_by_finwise_id(self, fw_id: str) -> Optional[dict]: ...
    def merchant_by_finwise_id(self, fw_id: str) -> Optional[dict]: ...
    def merchant_by_alias(self, alias: str) -> Optional[dict]: ...
    def accounts(self) -> Iterable[dict]: ...
    def merchants(self) -> Iterable[dict]: ...

    # --- Writes (each rebuilds indexes + atomic save) ---
    def add_account(self, alias: str, fw_record: dict, ynab_record: dict) -> dict: ...
    def add_merchant(self, alias: str, fw_record: dict, ynab_record: dict) -> dict: ...
    def attach_finwise_to_merchant(self, merchant_id: str, fw_record: dict) -> None: ...
    def refresh_records(self, fw_accounts=None, ynab_accounts=None, ynab_payees=None) -> None:
        """Overwrite cached finwise/ynab sub-records with freshly fetched data."""

    def _save(self) -> None:
        # write to .tmp then os.replace for atomicity
        ...
```

### Store decisions

- **`_normalize_alias`** strips whitespace and lowercases. The stored `alias` keeps the original casing for display; only the index key is normalized. So `"Easy Equities"` and `"easy equities"` resolve to the same merchant.
- **Atomic save.** Write to `config.json.tmp`, then `os.replace`. Prevents corruption on crash mid-write.
- **`refresh_records` is called by each phase** with the data that phase just fetched: Phase 1 passes `fw_accounts` and `ynab_accounts`; Phase 2 passes `ynab_payees`. It overwrites the corresponding cached sub-records on every stored entity, preventing rot from full-record nesting. Phase 1 and Phase 2 only handle *new* entities; existing ones are kept current by these refresh passes.
- **Indexes always rebuild on write**, never incrementally. Eliminates the class of bug where an incremental update misses an index entry.

## Phase 1 — Account Sync

Replaces the current `sync_accounts` function in `main.py`.

```python
def sync_accounts(fw_client, ynab_client, budget_id, store: ConfigStore):
    fw_accounts   = fw_client.get_accounts()
    ynab_accounts = ynab_client.get_accounts(budget_id)

    store.refresh_records(fw_accounts=fw_accounts, ynab_accounts=ynab_accounts)

    ynab_by_name = { _normalize_alias(a.name): a for a in ynab_accounts }

    for fw_acc in fw_accounts:
        if store.account_by_finwise_id(fw_acc.id):
            continue   # already linked

        alias = _prompt_alias_required(
            f"Enter YNAB account name for '{fw_acc.name}': ",
            default=fw_acc.name,
        )

        # Try existing YNAB account by name
        match = ynab_by_name.get(_normalize_alias(alias))
        if match:
            store.add_account(alias=alias, fw_record=fw_acc.dict(), ynab_record=match.dict())
            print(f"Linked '{fw_acc.name}' -> existing YNAB '{match.name}'")
            continue

        # Otherwise create on YNAB side
        ynab_new = ynab_client.create_account(
            budget_id,
            name=alias,
            type=_map_type(fw_acc),
            balance=_starting_balance(fw_acc, fw_client),
        )
        store.add_account(alias=alias, fw_record=fw_acc.dict(), ynab_record=ynab_new.dict())
        print(f"Created YNAB account '{alias}'")
```

### Phase 1 decisions

- **Iteration is over FinWise accounts only.** YNAB accounts that nothing in FinWise points to are ignored (likely manual entries).
- **Empty alias is rejected.** `_prompt_alias_required` re-prompts until the user enters a non-empty string.
- **`_starting_balance` preserves today's balance-adjustment math** (fetch transactions for the period, subtract from current balance to compute the true starting balance). Extracted as a helper, behavior unchanged.
- **No implicit alias state.** Today's code writes `account_aliases[name] = name` when names already match, just so the dict has an entry. In the new model, the entity exists or it doesn't.
- **Account creation is atomic with linking.** If the YNAB API call fails, `store.add_account` is never called and the next run re-prompts. No half-states.

## Phase 2 — Merchant-Payee Sync

New top-level step. Runs after `sync_accounts` and before the transaction pipeline. Replaces the per-transaction merchant prompting in `process_payee_aliases`.

```python
def sync_merchants(fw_client, ynab_client, budget_id, store: ConfigStore):
    fw_transactions = fw_client.get_transactions(start_date=_sync_start_date())
    ynab_payees     = ynab_client.get_payees(budget_id)

    store.refresh_records(ynab_payees=ynab_payees)

    fw_merchants = _extract_distinct_merchants(fw_transactions)
    ynab_by_name = { _normalize_alias(p.name): p for p in ynab_payees }

    for fw_m in fw_merchants:
        if store.merchant_by_finwise_id(fw_m.id):
            continue

        alias = _prompt_alias_required(
            f"Enter YNAB payee for merchant '{fw_m.name}' (id={fw_m.id}): ",
            default=fw_m.name,
        )

        # Existing merchant by alias? Attach this FW merchant to it.
        existing = store.merchant_by_alias(alias)
        if existing:
            store.attach_finwise_to_merchant(existing["id"], fw_m.dict())
            print(f"Attached FinWise merchant '{fw_m.name}' to existing '{existing['alias']}'")
            continue

        # Existing YNAB payee by name? Wrap it.
        ynab_match = ynab_by_name.get(_normalize_alias(alias))
        if ynab_match:
            store.add_merchant(alias=alias, fw_record=fw_m.dict(), ynab_record=ynab_match.dict())
            print(f"Linked merchant '{alias}' -> existing YNAB payee")
            continue

        # Create on YNAB side, then link
        ynab_new = ynab_client.create_payee(budget_id, name=alias)
        store.add_merchant(alias=alias, fw_record=fw_m.dict(), ynab_record=ynab_new.dict())
        print(f"Created YNAB payee '{alias}'")
```

### Phase 2 decisions

- **Iterates distinct FinWise merchants, not transactions.** `_extract_distinct_merchants` walks all FW transactions in the sync window and emits one record per unique `merchant_id`. 12 transactions sharing a merchant ID produce one prompt, not twelve.
- **Alias-dedup before YNAB-create.** This is the multi-source-merchants story. Typing `"Easy Equities"` a second time attaches the new FW id to the existing merchant entity. No duplicate YNAB payee gets created.
- **Empty alias is rejected** (same `_prompt_alias_required` helper as Phase 1).
- **No in-session ignore-set.** Each distinct merchant is prompted at most once per run; an in-run cache is unnecessary.
- **`create_payee` is a real API call** (see SDK migration below). No deferred-creation hack; every stored merchant always has both `finwise` and `ynab` populated.
- **Merchant fetch window matches sync window.** Phase 2 fetches FW transactions for the same date range the transaction sync will use, so the set of merchants Phase 2 sees is a superset of what the transaction sync needs.

## YNAB SDK Migration

The currently-installed `ynab-api` package (v2.0.2, last released 2023-07-03) is unmaintained and exposes no payee-creation method. The official SDK at `github.com/ynab/ynab-sdk-python` (PyPI: `ynab`, v4.1.0) wraps the modern YNAB API and includes `create_payee`. Migration happens in this work.

### Migration tasks

- `pyproject.toml`: replace `ynab-api>=2.0.2` with `ynab>=4.1.0`.
- `uv.lock`: regenerate via `uv sync`.
- `src/finab/ynab_client.py`: re-point every call to the new SDK's class/method names (`ynab.api.payees_api.PayeesApi`, etc.). Maintain the existing `YNABClient` facade so callers don't change.
- Add `YNABClient.create_payee(budget_id, name) -> Payee` using `PayeesApi.create_payee`.

### Migration risks

- The new SDK has a different generated model layer. Field access (`.id`, `.name`, etc.) should be stable, but `.to_dict()` / `.dict()` shapes may differ. Audit each callsite.
- Authentication mechanism (bearer token from `YNAB_ACCESS_TOKEN`) is unchanged in the new SDK.
- No behavior change is expected for existing endpoints (`get_accounts`, `get_transactions`, etc.).

## Integration with the Existing Transaction Pipeline

The transaction sync (Phase 3) is out of scope. The pipeline code (`map_accounts`, `process_payee_aliases`, `process_categories`, etc.) currently reads legacy `account_aliases` and `merchant_aliases` dicts via `config.py`. Strategy: **thin compatibility shims** that synthesize the legacy dict shape from the new `ConfigStore`, so pipeline code is left untouched.

```python
# src/finab/config.py — replaces old load_aliases / load_merchant_aliases

def load_aliases(store: ConfigStore | None = None) -> dict[str, str]:
    """Legacy shape: {finwise_account_name: ynab_alias}, synthesized from the store."""
    store = store or ConfigStore()
    return { a["finwise"]["name"]: a["alias"] for a in store.accounts() }

def load_merchant_aliases(store: ConfigStore | None = None) -> dict[str, str]:
    """Legacy shape: {finwise_merchant_id: alias}, flattened from 1:many merchants."""
    store = store or ConfigStore()
    return {
        fw_id: m["alias"]
        for m in store.merchants()
        for fw_id in m["finwise"]
    }
```

### Removed and converted call sites

- **`save_aliases`** (config.py): removed. Two callsites in the old `sync_accounts` go away when that function is replaced.
- **`save_merchant_aliases`** (config.py): removed. Four callsites in `process_payee_aliases` and `process_categories` are converted mechanically:
  - `merchant_aliases[fw_id] = alias` → `store.add_merchant(...)` (for genuinely new merchants) or `store.attach_finwise_to_merchant(...)` (when the alias matches an existing merchant).
- **No logic changes** to the prompting code in `process_payee_aliases` or `process_categories`. After Phase 2, those branches are effectively unreachable but remain as defensive fallbacks.

## File Changes Summary

| File | Change |
|---|---|
| `src/finab/store.py` | **new** — `ConfigStore` class with in-memory indexes, atomic save, `refresh_records`. |
| `src/finab/config.py` | Remove `load_aliases/save_aliases`, `load_merchant_aliases/save_merchant_aliases`. Add thin shims that read from `ConfigStore`. |
| `src/finab/main.py` | Rewrite `sync_accounts`. Add `sync_merchants`. Convert four `save_merchant_aliases` callsites to `store.add_merchant` / `store.attach_finwise_to_merchant`. |
| `src/finab/ynab_client.py` | Migrate from `ynab-api` to `ynab`. Add `create_payee`. |
| `pyproject.toml`, `uv.lock` | Drop `ynab-api`, add `ynab>=4.1.0`. |
| `config.json` (user data) | No manual edit required. New code simply does not read `account_aliases` or `merchant_aliases`; the keys persist as dead data until the user removes them. `accounts` and `merchants` start empty and rebuild on the next run. |

## Open Questions for Implementation

These were intentionally not resolved at design time; flag during implementation:

- **Account currency mismatch.** Today's `create_account` carries currency from FinWise. YNAB account creation may not accept a `currency_code` parameter (budget-level setting). Confirm behavior in the new SDK.
- **Starting balance for credit accounts.** The existing balance-adjustment logic assumes assets. Verify the sign convention still works for credit/loan accounts in the new SDK's account-creation payload.
- **FinWise merchant record shape.** `_extract_distinct_merchants` needs to know which fields on `FinWiseTransaction` constitute the "merchant record." Current model has `merchant_id`, `merchant_name`, `original_merchant_id`. The full FinWise merchant record nested under `merchants.<x>.finwise.<fw_id>` should at minimum include all three.

## Non-Goals

- Reworking the transaction sync, categorization, splits, or transfer detection.
- Touching `payee_rules` or `categories` regex rule sets.
- Auto-migration from the legacy `config.json` keys (user opted for fresh start).
- Stable-IDs-over-names protection (renames are tolerated via per-run record refresh, not optimized for).
- Multi-user or concurrent-run safety (single-user CLI tool).
