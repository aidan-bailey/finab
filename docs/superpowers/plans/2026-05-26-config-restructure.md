# Config Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `config.json` so accounts and merchants are first-class entities with full FinWise/YNAB records nested under each, and migrate the YNAB client from the unmaintained `ynab-api` to the official `ynab` SDK so `create_payee` is available.

**Architecture:** A new `ConfigStore` (`src/finab/store.py`) owns the new schema. All reads go through O(1) in-memory indexes; writes only ever touch the primary store and trigger an index rebuild + atomic save. Two new top-level flow steps — `sync_accounts` (rewritten) and `sync_merchants` (new) — populate the store before the transaction pipeline runs. The existing transaction code is left untouched and reads the store via thin compatibility shims in `config.py`.

**Tech Stack:** Python 3.14, `ynab>=4.1.0` (official SDK, replaces `ynab-api`), `finwise-python>=1.4.0`, pytest, `uv` package manager.

**Reference spec:** `docs/superpowers/specs/2026-05-26-config-restructure-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Replace `ynab-api>=2.0.2` with `ynab>=4.1.0`. |
| `src/finab/store.py` | **Create** | `ConfigStore` class: schema persistence, in-memory indexes, atomic save, `refresh_records`. |
| `src/finab/ynab_client.py` | Modify | Migrate every call site to `ynab` SDK. Add `create_payee`. Preserve the `YNABClient` facade. |
| `src/finab/config.py` | Modify | Remove `load_aliases/save_aliases`, `load_merchant_aliases/save_merchant_aliases`. Add store-backed compatibility shims for `load_aliases` and `load_merchant_aliases`. |
| `src/finab/main.py` | Modify | Replace `sync_accounts`. Add `sync_merchants`. Add `_normalize_alias` and `_prompt_alias_required` helpers. Convert 4 in-pipeline `save_merchant_aliases` callsites to `store.*` calls. Wire `main()` to instantiate `ConfigStore` and call both syncs. |
| `tests/test_store.py` | **Create** | Unit tests for `ConfigStore`. |
| `tests/test_sync_accounts.py` | **Create** | Unit tests for the new `sync_accounts`. |
| `tests/test_sync_merchants.py` | **Create** | Unit tests for `sync_merchants` and `_extract_distinct_merchants`. |
| `tests/test_helpers.py` | **Create** | Unit tests for `_normalize_alias` and `_prompt_alias_required`. |
| `tests/test_ynab_create_payee.py` | **Create** | Unit test verifying `YNABClient.create_payee` calls the new SDK correctly. |

---

## Task 1: Swap `ynab-api` → `ynab` in dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Edit `pyproject.toml` to replace the dependency**

Replace the `dependencies` block so it reads exactly:

```toml
dependencies = [
    "finwise-python>=1.4.0",
    "python-dotenv>=1.2.1",
    "ynab>=4.1.0",
]
```

- [ ] **Step 2: Resolve and install**

Run: `uv sync`
Expected: `uv` prints `Resolved N packages` and installs `ynab` (current `4.1.0` or later). The `ynab-api` package is removed from the lockfile.

- [ ] **Step 3: Smoke-test the import**

Run: `uv run python -c "import ynab; from ynab.api.payees_api import PayeesApi; print(PayeesApi.create_payee.__doc__[:80])"`
Expected: prints the first 80 chars of the `create_payee` docstring (proves the new SDK is installed and `create_payee` is exposed).

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: swap ynab-api for official ynab SDK

Drops the unmaintained ynab-api (last released 2023-07) for the
official ynab SDK, which exposes create_payee and tracks the current
YNAB API spec."
```

---

## Task 2: Migrate `YNABClient` methods to the `ynab` SDK

**Files:**
- Modify: `src/finab/ynab_client.py`

The new SDK uses different module paths but the same authentication and response shapes. We replace every import and call while preserving every public method signature.

**Pre-task check — verify model import paths.** The exact module names in the `ynab` SDK (e.g., `NewTransaction` vs. `SaveTransaction`, `PostTransactionsWrapper` vs. `SaveTransactionsWrapper`) are generated from the OpenAPI spec and have changed across SDK versions. Before editing imports, run:

```bash
uv run python -c "import ynab; import pkgutil; [print(m.name) for m in pkgutil.iter_modules(ynab.models.__path__)]" | sort
```

Cross-reference the printed module names against the imports in Step 1 below. If any import name doesn't match a printed module, substitute the correct one (e.g., if the SDK exposes `save_transaction` instead of `new_transaction`, update the import accordingly). The semantics are stable; only the names rotate.

- [ ] **Step 1: Replace imports at top of `src/finab/ynab_client.py`**

Replace lines 1–19 (the entire import block plus the immediate stdlib imports) with:

```python
import os
from datetime import date
from typing import Any, List, Optional

import dataclasses
from dotenv import load_dotenv

import ynab
from ynab.api.accounts_api import AccountsApi
from ynab.api.budgets_api import BudgetsApi
from ynab.api.categories_api import CategoriesApi
from ynab.api.payees_api import PayeesApi
from ynab.api.transactions_api import TransactionsApi
from ynab.models.save_account import SaveAccount
from ynab.models.post_account_wrapper import PostAccountWrapper
from ynab.models.new_transaction import NewTransaction
from ynab.models.post_transactions_wrapper import PostTransactionsWrapper
from ynab.models.existing_transaction import ExistingTransaction
from ynab.models.patch_transactions_wrapper import PatchTransactionsWrapper
from ynab.models.save_sub_transaction import SaveSubTransaction

from finab.models import YNABTransaction, Transaction, Account
```

- [ ] **Step 2: Replace `__init__` to use the new `Configuration` / `ApiClient`**

Replace the body of `__init__` (currently lines 25–60) with:

```python
def __init__(self, api_key: Optional[str] = None):
    load_dotenv()

    self.api_key = api_key or os.getenv("YNAB_ACCESS_TOKEN")
    if not self.api_key:
        raise ValueError(
            "YNAB_ACCESS_TOKEN environment variable is not set and no api_key provided."
        )

    if self.api_key.lower().startswith("bearer "):
        self.api_key = self.api_key[7:].strip()

    self.configuration = ynab.Configuration(access_token=self.api_key)
    self.api_client = ynab.ApiClient(self.configuration)
```

The new SDK takes `access_token` directly on `Configuration` — no manual bearer header setup, no `client_side_validation` flag.

- [ ] **Step 3: Replace `get_budgets`**

Replace the existing method body with:

```python
def get_budgets(self) -> Any:
    budgets_api = BudgetsApi(self.api_client)
    response = budgets_api.get_budgets()
    return response.data.budgets
```

The new SDK deserializes correctly, so the `_preload_content=False` workaround for `default_budget` is gone.

- [ ] **Step 4: Replace `get_transactions`**

```python
def get_transactions(
    self,
    budget_id: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> Any:
    transactions_api = TransactionsApi(self.api_client)

    kwargs = {}
    if start_date:
        kwargs["since_date"] = start_date

    response = transactions_api.get_transactions(budget_id, **kwargs)
    transactions = response.data.transactions

    if end_date:
        transactions = [t for t in transactions if t.var_date <= end_date]

    return transactions
```

Note: the new SDK exposes the `date` field as `var_date` because `date` collides with the stdlib type at model-generation time. Update the call site filtering accordingly.

- [ ] **Step 5: Replace `get_accounts`**

```python
def get_accounts(self, budget_id: str) -> List[Account]:
    accounts_api = AccountsApi(self.api_client)
    response = accounts_api.get_accounts(budget_id)

    accounts = []
    for acc in response.data.accounts:
        if acc.closed:
            continue
        accounts.append(
            Account(
                name=acc.name,
                type=acc.type,
                balance=acc.balance,
                currency_code="",
                finwise_id=None,
                ynab_id=acc.id,
                transfer_payee_id=acc.transfer_payee_id,
            )
        )
    return accounts
```

- [ ] **Step 6: Replace `get_payees`**

```python
def get_payees(self, budget_id: str) -> List[Any]:
    payees_api = PayeesApi(self.api_client)
    response = payees_api.get_payees(budget_id)
    return response.data.payees
```

- [ ] **Step 7: Replace `get_categories`**

```python
def get_categories(self, budget_id: str) -> List[Any]:
    categories_api = CategoriesApi(self.api_client)
    response = categories_api.get_categories(budget_id)
    all_categories = []
    for group in response.data.category_groups:
        all_categories.extend(group.categories)
    return all_categories
```

- [ ] **Step 8: Replace `create_account`**

```python
def create_account(self, budget_id: str, account: Account) -> Any:
    accounts_api = AccountsApi(self.api_client)
    save_account = SaveAccount(
        name=account.name, type=account.type, balance=account.balance
    )
    data = PostAccountWrapper(account=save_account)
    return accounts_api.create_account(budget_id, data)
```

- [ ] **Step 9: Replace `create_transactions`**

```python
def create_transactions(
    self,
    budget_id: str,
    transactions: list[dict | NewTransaction | YNABTransaction | Transaction],
) -> Any:
    transactions_api = TransactionsApi(self.api_client)

    new_transactions = []
    for txn in transactions:
        if isinstance(txn, Transaction):
            txn = txn.to_ynab()

        if isinstance(txn, dict):
            new_transactions.append(NewTransaction(**txn))
        elif isinstance(txn, YNABTransaction):
            txn_dict = {
                k: v for k, v in dataclasses.asdict(txn).items() if v is not None
            }
            if "subtransactions" in txn_dict:
                txn_dict["subtransactions"] = [
                    SaveSubTransaction(**sub) if isinstance(sub, dict) else sub
                    for sub in txn_dict["subtransactions"]
                ]
            new_transactions.append(NewTransaction(**txn_dict))
        else:
            new_transactions.append(txn)

    data = PostTransactionsWrapper(transactions=new_transactions)
    return transactions_api.create_transaction(budget_id, data)
```

- [ ] **Step 10: Replace `update_transactions`**

```python
def update_transactions(
    self,
    budget_id: str,
    transactions: list[dict | YNABTransaction | Transaction],
) -> Any:
    transactions_api = TransactionsApi(self.api_client)

    existing_list = []
    for txn in transactions:
        txn_dict = {}
        if isinstance(txn, Transaction):
            if not txn.ynab_id:
                continue
            txn_dict = {
                "id": txn.ynab_id,
                "account_id": txn.account_id,
                "date": txn.date,
                "amount": txn.amount,
                "payee_id": txn.payee_id,
                "payee_name": txn.payee_name,
                "category_id": txn.category_id,
                "memo": txn.memo,
                "cleared": txn.cleared,
                "approved": txn.approved,
                "flag_color": txn.flag_color,
                "import_id": txn.import_id,
            }
            if txn.subtransactions:
                txn_dict["subtransactions"] = [
                    SaveSubTransaction(**sub) if isinstance(sub, dict) else sub
                    for sub in txn.subtransactions
                ]
        elif isinstance(txn, dict):
            txn_dict = txn

        txn_dict = {k: v for k, v in txn_dict.items() if v is not None}
        if "id" not in txn_dict:
            continue

        existing_list.append(ExistingTransaction(**txn_dict))

    if not existing_list:
        return None

    data = PatchTransactionsWrapper(transactions=existing_list)
    return transactions_api.update_transactions(budget_id, data)
```

- [ ] **Step 11: Replace `delete_transaction`**

```python
def delete_transaction(self, budget_id: str, transaction_id: str) -> Any:
    transactions_api = TransactionsApi(self.api_client)
    return transactions_api.delete_transaction(budget_id, transaction_id)
```

The new SDK has a real `delete_transaction` method, so the manual `call_api` workaround at the bottom of the old client is gone.

- [ ] **Step 12: Run the existing test suite**

Run: `uv run pytest -v`
Expected: All tests still pass. The existing tests mock `finab.ynab_client` so they don't exercise the SDK directly; they verify the public surface of `YNABClient` is unchanged.

If any test fails because of an attribute name change on a response object (most likely `t.date` → `t.var_date`), fix it inline in the test before moving on.

- [ ] **Step 13: Commit**

```bash
git add src/finab/ynab_client.py tests/
git commit -m "refactor(ynab): migrate YNABClient to official ynab SDK

Drop ynab-api swagger-codegen client for ynab. Public facade
(YNABClient methods + signatures) is unchanged so callers don't move;
only internal SDK calls swap. Removes workarounds for default_budget
deserialization and the manual delete_transaction call_api shim, both
of which the new SDK handles natively."
```

---

## Task 3: Add `YNABClient.create_payee` (TDD)

**Files:**
- Modify: `src/finab/ynab_client.py`
- Create: `tests/test_ynab_create_payee.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_ynab_create_payee.py`:

```python
import unittest
from unittest.mock import MagicMock, patch

from finab.ynab_client import YNABClient


class TestCreatePayee(unittest.TestCase):
    @patch("finab.ynab_client.PayeesApi")
    def test_create_payee_calls_sdk(self, mock_payees_api_cls):
        mock_api = MagicMock()
        mock_payees_api_cls.return_value = mock_api
        mock_response = MagicMock()
        mock_response.data.payee = MagicMock(id="payee-123", name="Test Payee")
        mock_api.create_payee.return_value = mock_response

        client = YNABClient(api_key="test-token")
        result = client.create_payee("budget-1", "Test Payee")

        mock_payees_api_cls.assert_called_once_with(client.api_client)
        # The wrapper passed to create_payee must carry name=Test Payee
        call_args = mock_api.create_payee.call_args
        self.assertEqual(call_args.args[0], "budget-1")
        wrapper = call_args.args[1]
        self.assertEqual(wrapper.payee.name, "Test Payee")

        self.assertEqual(result.id, "payee-123")
        self.assertEqual(result.name, "Test Payee")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test, expect fail**

Run: `uv run pytest tests/test_ynab_create_payee.py -v`
Expected: FAIL with `AttributeError: 'YNABClient' object has no attribute 'create_payee'`.

- [ ] **Step 3: Add the import for the payee wrapper and `SavePayee` to `ynab_client.py`**

Add to the existing import block:

```python
from ynab.models.save_payee import SavePayee
from ynab.models.post_payee_wrapper import PostPayeeWrapper
```

- [ ] **Step 4: Add `create_payee` method to `YNABClient`**

Insert this method in `src/finab/ynab_client.py`, placed alongside the other `create_*` methods:

```python
def create_payee(self, budget_id: str, name: str) -> Any:
    """Create a new payee in the budget. Returns the created payee record."""
    payees_api = PayeesApi(self.api_client)
    wrapper = PostPayeeWrapper(payee=SavePayee(name=name))
    response = payees_api.create_payee(budget_id, wrapper)
    return response.data.payee
```

- [ ] **Step 5: Run the test, expect pass**

Run: `uv run pytest tests/test_ynab_create_payee.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/finab/ynab_client.py tests/test_ynab_create_payee.py
git commit -m "feat(ynab): add YNABClient.create_payee

Wraps the official SDK's PayeesApi.create_payee. Returns the created
payee record so callers can immediately link it into the local store."
```

---

## Task 4: Create `ConfigStore` skeleton (load, save, init)

**Files:**
- Create: `src/finab/store.py`
- Create: `tests/test_store.py`

- [ ] **Step 1: Write the failing test for load + save round-trip**

Create `tests/test_store.py`:

```python
import json
import unittest
from pathlib import Path
import tempfile

from finab.store import ConfigStore


class TestConfigStoreBasics(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_missing_file_returns_empty_store(self):
        store = ConfigStore(self.path)
        self.assertEqual(list(store.accounts()), [])
        self.assertEqual(list(store.merchants()), [])

    def test_load_preserves_unrelated_keys(self):
        self.path.write_text(json.dumps({
            "budget_id": "abc",
            "payee_rules": [{"pattern": "x", "target": "y"}],
            "accounts": {},
            "merchants": {},
        }))
        store = ConfigStore(self.path)
        # Unrelated keys round-trip through the data dict
        self.assertEqual(store._data["budget_id"], "abc")
        self.assertEqual(store._data["payee_rules"], [{"pattern": "x", "target": "y"}])

    def test_atomic_save_writes_via_tmp(self):
        store = ConfigStore(self.path)
        store._data["sentinel"] = "value"
        store._save()
        self.assertTrue(self.path.exists())
        with open(self.path) as f:
            self.assertEqual(json.load(f)["sentinel"], "value")
        # No leftover .tmp file
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test, expect fail**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finab.store'`.

- [ ] **Step 3: Create `src/finab/store.py` with minimal implementation**

```python
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional


CONFIG_FILE = Path("config.json")


def _normalize_alias(alias: str) -> str:
    return alias.strip().lower()


class ConfigStore:
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = Path(path)
        self._data: dict = self._load()
        self._data.setdefault("accounts", {})
        self._data.setdefault("merchants", {})
        self._rebuild_indexes()

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}

    def _save(self) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, indent=4, default=str)
        os.replace(tmp, self.path)

    def _rebuild_indexes(self) -> None:
        self._fw_account_index: dict[str, str] = {}
        self._fw_merchant_index: dict[str, str] = {}
        self._alias_merchant_index: dict[str, str] = {}

        for acc in self._data.get("accounts", {}).values():
            fw = acc.get("finwise", {})
            if fw.get("id"):
                self._fw_account_index[fw["id"]] = acc["id"]

        for m in self._data.get("merchants", {}).values():
            for fw_id in m.get("finwise", {}):
                self._fw_merchant_index[fw_id] = m["id"]
            if m.get("alias"):
                self._alias_merchant_index[_normalize_alias(m["alias"])] = m["id"]

    def accounts(self) -> Iterable[dict]:
        return self._data["accounts"].values()

    def merchants(self) -> Iterable[dict]:
        return self._data["merchants"].values()
```

The test for `test_atomic_save_writes_via_tmp` checks the tmp path `self.path.with_suffix(".json.tmp")`. The implementation above writes to `self.path.with_suffix(self.path.suffix + ".tmp")` which evaluates to `config.json.tmp`. The test's check using `.with_suffix(".json.tmp")` produces the same path because `Path("config.json").with_suffix(".json.tmp")` returns `Path("config.json.tmp")`. The check passes.

- [ ] **Step 4: Run the test, expect pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): scaffold ConfigStore with load, save, indexes

Atomic save via .tmp + os.replace prevents corruption on crash mid-write.
Indexes are always rebuilt from the primary store; never persisted."
```

---

## Task 5: `ConfigStore` — account API

**Files:**
- Modify: `src/finab/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Add failing tests for account API**

Append to `tests/test_store.py`:

```python
class TestConfigStoreAccounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_account_creates_uuid_and_persists(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-1", "name": "Checking"}
        yn = {"id": "yn-1", "name": "Checking"}

        acc = store.add_account(alias="Checking", fw_record=fw, ynab_record=yn)

        self.assertEqual(acc["alias"], "Checking")
        self.assertEqual(acc["finwise"], fw)
        self.assertEqual(acc["ynab"], yn)
        self.assertTrue(acc["id"])

        # Reload from disk: data round-trips
        store2 = ConfigStore(self.path)
        self.assertEqual(list(store2.accounts()), [acc])

    def test_account_by_finwise_id_lookup(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-7", "name": "Savings"}
        yn = {"id": "yn-7", "name": "Savings"}
        acc = store.add_account(alias="Savings", fw_record=fw, ynab_record=yn)

        self.assertEqual(store.account_by_finwise_id("fw-7"), acc)
        self.assertIsNone(store.account_by_finwise_id("fw-missing"))

    def test_indexes_rebuild_after_add(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="A", fw_record={"id": "fw-A", "name": "A"}, ynab_record={"id": "yn-A"}
        )
        store.add_account(
            alias="B", fw_record={"id": "fw-B", "name": "B"}, ynab_record={"id": "yn-B"}
        )

        self.assertIn("fw-A", store._fw_account_index)
        self.assertIn("fw-B", store._fw_account_index)
        self.assertNotEqual(
            store._fw_account_index["fw-A"], store._fw_account_index["fw-B"]
        )
```

- [ ] **Step 2: Run tests, expect fail**

Run: `uv run pytest tests/test_store.py::TestConfigStoreAccounts -v`
Expected: FAIL with `AttributeError: 'ConfigStore' object has no attribute 'add_account'`.

- [ ] **Step 3: Implement `add_account` and `account_by_finwise_id`**

Add to `src/finab/store.py`:

```python
import uuid


# Inside class ConfigStore, append:

    def add_account(self, alias: str, fw_record: dict, ynab_record: dict) -> dict:
        internal_id = str(uuid.uuid4())
        account = {
            "id": internal_id,
            "alias": alias,
            "finwise": dict(fw_record),
            "ynab": dict(ynab_record),
        }
        self._data["accounts"][internal_id] = account
        self._rebuild_indexes()
        self._save()
        return account

    def account_by_finwise_id(self, fw_id: str) -> Optional[dict]:
        internal_id = self._fw_account_index.get(fw_id)
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (6 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): account add + lookup by finwise id

UUID4 internal id, full nested finwise/ynab records, index rebuilt and
saved on every write."
```

---

## Task 6: `ConfigStore` — merchant API

**Files:**
- Modify: `src/finab/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Add failing tests for merchant API**

Append to `tests/test_store.py`:

```python
class TestConfigStoreMerchants(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_add_merchant_keeps_finwise_as_dict_keyed_by_fw_id(self):
        store = ConfigStore(self.path)
        fw = {"id": "fw-m-1", "name": "Spar"}
        yn = {"id": "yn-p-1", "name": "Spar"}

        m = store.add_merchant(alias="Spar", fw_record=fw, ynab_record=yn)

        self.assertEqual(m["alias"], "Spar")
        self.assertEqual(m["finwise"], {"fw-m-1": fw})
        self.assertEqual(m["ynab"], yn)

    def test_merchant_by_finwise_id_finds_any_finwise_child(self):
        store = ConfigStore(self.path)
        fw_a = {"id": "fw-m-A", "name": "Easy Equities"}
        fw_b = {"id": "fw-m-B", "name": "Easy Equities"}
        yn = {"id": "yn-p-EE", "name": "Easy Equities"}

        m = store.add_merchant(alias="Easy Equities", fw_record=fw_a, ynab_record=yn)
        store.attach_finwise_to_merchant(m["id"], fw_b)

        self.assertEqual(store.merchant_by_finwise_id("fw-m-A")["id"], m["id"])
        self.assertEqual(store.merchant_by_finwise_id("fw-m-B")["id"], m["id"])

    def test_merchant_by_alias_normalizes_lookup(self):
        store = ConfigStore(self.path)
        store.add_merchant(
            alias="Pick n Pay",
            fw_record={"id": "fw-pnp", "name": "PnP"},
            ynab_record={"id": "yn-pnp", "name": "Pick n Pay"},
        )

        # Exact match
        self.assertIsNotNone(store.merchant_by_alias("Pick n Pay"))
        # Lowercased + whitespace tolerant
        self.assertIsNotNone(store.merchant_by_alias("  pick n pay  "))
        # Different alias misses
        self.assertIsNone(store.merchant_by_alias("Checkers"))

    def test_attach_finwise_to_merchant_persists(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Shell",
            fw_record={"id": "fw-shell-1", "name": "Shell"},
            ynab_record={"id": "yn-shell", "name": "Shell"},
        )
        store.attach_finwise_to_merchant(m["id"], {"id": "fw-shell-2", "name": "Shell"})

        # Reload from disk to ensure persistence
        store2 = ConfigStore(self.path)
        m2 = store2.merchant_by_finwise_id("fw-shell-2")
        self.assertEqual(m2["id"], m["id"])
        self.assertIn("fw-shell-1", m2["finwise"])
        self.assertIn("fw-shell-2", m2["finwise"])
```

- [ ] **Step 2: Run tests, expect fail**

Run: `uv run pytest tests/test_store.py::TestConfigStoreMerchants -v`
Expected: FAIL — methods don't exist yet.

- [ ] **Step 3: Implement the merchant API**

Add to `ConfigStore` in `src/finab/store.py`:

```python
    def add_merchant(self, alias: str, fw_record: dict, ynab_record: dict) -> dict:
        internal_id = str(uuid.uuid4())
        fw_id = fw_record["id"]
        merchant = {
            "id": internal_id,
            "alias": alias,
            "finwise": {fw_id: dict(fw_record)},
            "ynab": dict(ynab_record),
        }
        self._data["merchants"][internal_id] = merchant
        self._rebuild_indexes()
        self._save()
        return merchant

    def attach_finwise_to_merchant(self, merchant_id: str, fw_record: dict) -> None:
        merchant = self._data["merchants"][merchant_id]
        merchant["finwise"][fw_record["id"]] = dict(fw_record)
        self._rebuild_indexes()
        self._save()

    def merchant_by_finwise_id(self, fw_id: str) -> Optional[dict]:
        internal_id = self._fw_merchant_index.get(fw_id)
        if not internal_id:
            return None
        return self._data["merchants"][internal_id]

    def merchant_by_alias(self, alias: str) -> Optional[dict]:
        internal_id = self._alias_merchant_index.get(_normalize_alias(alias))
        if not internal_id:
            return None
        return self._data["merchants"][internal_id]
```

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (10 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): merchant add, attach, lookup by id and alias

merchant.finwise is a dict keyed by finwise_merchant_id so the same
internal merchant can absorb multiple FinWise IDs (Easy Equities,
Lifestyle on Kloof, FNB Aspire Credit Account all need this in the
current data)."
```

---

## Task 7: `ConfigStore.refresh_records`

**Files:**
- Modify: `src/finab/store.py`
- Modify: `tests/test_store.py`

- [ ] **Step 1: Add failing test**

Append to `tests/test_store.py`:

```python
class TestRefreshRecords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_refresh_overwrites_finwise_record(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="A",
            fw_record={"id": "fw-A", "name": "Old Name", "balance": 100},
            ynab_record={"id": "yn-A", "name": "A"},
        )

        # Build a fake FinWise account with updated fields
        class FakeFW:
            id = "fw-A"
            def dict(self):
                return {"id": "fw-A", "name": "New Name", "balance": 200}

        store.refresh_records(fw_accounts=[FakeFW()])

        acc = store.account_by_finwise_id("fw-A")
        self.assertEqual(acc["finwise"]["name"], "New Name")
        self.assertEqual(acc["finwise"]["balance"], 200)

    def test_refresh_overwrites_ynab_payee_on_merchant(self):
        store = ConfigStore(self.path)
        store.add_merchant(
            alias="Shell",
            fw_record={"id": "fw-shell", "name": "Shell"},
            ynab_record={"id": "yn-shell", "name": "Shell (old)"},
        )

        class FakeYNAB:
            id = "yn-shell"
            def to_dict(self):
                return {"id": "yn-shell", "name": "Shell"}

        store.refresh_records(ynab_payees=[FakeYNAB()])

        m = store.merchant_by_finwise_id("fw-shell")
        self.assertEqual(m["ynab"]["name"], "Shell")

    def test_refresh_ignores_unknown_records(self):
        store = ConfigStore(self.path)

        class FakeFW:
            id = "fw-unknown"
            def dict(self):
                return {"id": "fw-unknown"}

        # Should not raise even though no account is linked
        store.refresh_records(fw_accounts=[FakeFW()])
```

- [ ] **Step 2: Run tests, expect fail**

Run: `uv run pytest tests/test_store.py::TestRefreshRecords -v`
Expected: FAIL.

- [ ] **Step 3: Implement `refresh_records`**

Add to `ConfigStore`:

```python
    def refresh_records(
        self,
        fw_accounts=None,
        ynab_accounts=None,
        ynab_payees=None,
    ) -> None:
        """Overwrite cached finwise/ynab sub-records with freshly fetched data."""
        changed = False

        if fw_accounts:
            for fw in fw_accounts:
                acc = self.account_by_finwise_id(fw.id)
                if acc:
                    acc["finwise"] = to_dict(fw)
                    changed = True

        if ynab_accounts:
            yn_by_id = {y.ynab_id or y.id: y for y in ynab_accounts}
            for acc in self.accounts():
                yn_id = acc["ynab"].get("id")
                if yn_id and yn_id in yn_by_id:
                    acc["ynab"] = to_dict(yn_by_id[yn_id])
                    changed = True

        if ynab_payees:
            yn_by_id = {p.id: p for p in ynab_payees}
            for m in self.merchants():
                yn_id = m["ynab"].get("id")
                if yn_id and yn_id in yn_by_id:
                    m["ynab"] = to_dict(yn_by_id[yn_id])
                    changed = True

        if changed:
            self._save()
```

And add `to_dict` (no leading underscore — exported for use by `main.py`) near `_normalize_alias` at the top of `src/finab/store.py`:

```python
def to_dict(obj) -> dict:
    """Convert a pydantic-like or dataclass-like object to a plain dict."""
    if isinstance(obj, dict):
        return obj
    for method_name in ("model_dump", "to_dict", "dict"):
        method = getattr(obj, method_name, None)
        if callable(method):
            return method()
    # Fall back to __dict__ (dataclasses, simple objects)
    return dict(obj.__dict__)
```

Update the calls inside `refresh_records` from `_to_dict(...)` to `to_dict(...)` (three callsites).

- [ ] **Step 4: Run tests, expect pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS (13 tests total).

- [ ] **Step 5: Commit**

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): refresh_records keeps nested records fresh each run

Phase 1 calls with (fw_accounts, ynab_accounts); Phase 2 with
ynab_payees. Overwrites the cached side of each stored entity from
freshly fetched data, preventing rot from full-record nesting."
```

---

## Task 8: Compatibility shims in `config.py`

**Files:**
- Modify: `src/finab/config.py`
- Create: `tests/test_config_shims.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_shims.py`:

```python
import unittest
import tempfile
from pathlib import Path

from finab.config import load_aliases, load_merchant_aliases
from finab.store import ConfigStore


class TestConfigShims(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_load_aliases_synthesizes_from_store(self):
        store = ConfigStore(self.path)
        store.add_account(
            alias="Checking",
            fw_record={"id": "fw-1", "name": "FinWise Checking"},
            ynab_record={"id": "yn-1", "name": "Checking"},
        )
        store.add_account(
            alias="Savings",
            fw_record={"id": "fw-2", "name": "FinWise Savings"},
            ynab_record={"id": "yn-2", "name": "Savings"},
        )

        aliases = load_aliases(store=store)
        self.assertEqual(
            aliases,
            {"FinWise Checking": "Checking", "FinWise Savings": "Savings"},
        )

    def test_load_merchant_aliases_flattens_one_to_many(self):
        store = ConfigStore(self.path)
        m = store.add_merchant(
            alias="Easy Equities",
            fw_record={"id": "fw-m-A", "name": "EE"},
            ynab_record={"id": "yn-p-EE", "name": "Easy Equities"},
        )
        store.attach_finwise_to_merchant(m["id"], {"id": "fw-m-B", "name": "EE"})

        aliases = load_merchant_aliases(store=store)
        self.assertEqual(
            aliases,
            {"fw-m-A": "Easy Equities", "fw-m-B": "Easy Equities"},
        )

    def test_save_aliases_no_longer_exists(self):
        import finab.config as config_module
        self.assertFalse(hasattr(config_module, "save_aliases"))
        self.assertFalse(hasattr(config_module, "save_merchant_aliases"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_config_shims.py -v`
Expected: FAIL — current `load_aliases` doesn't accept a `store` kwarg and `save_aliases` still exists.

- [ ] **Step 3: Replace the alias functions in `src/finab/config.py`**

Open `src/finab/config.py` and:

1. Add an import near the top: `from finab.store import ConfigStore`.
2. Delete the four functions `load_aliases`, `save_aliases`, `load_merchant_aliases`, `save_merchant_aliases` (currently lines 26–36 and 65–75).
3. Insert in their place:

```python
def load_aliases(store: Optional[ConfigStore] = None) -> Dict[str, str]:
    """Backward-compat shim: returns {finwise_account_name: ynab_alias}.

    Synthesized from the new ConfigStore so callers in the transaction
    pipeline keep working without changes.
    """
    store = store or ConfigStore()
    return {acc["finwise"]["name"]: acc["alias"] for acc in store.accounts()}


def load_merchant_aliases(store: Optional[ConfigStore] = None) -> Dict[str, str]:
    """Backward-compat shim: returns {finwise_merchant_id: alias}.

    Flattens the 1:many merchants store into the legacy flat dict shape.
    """
    store = store or ConfigStore()
    return {
        fw_id: m["alias"]
        for m in store.merchants()
        for fw_id in m["finwise"]
    }
```

- [ ] **Step 3b: Drop the deleted symbols from `src/finab/main.py`'s import block**

The existing `from finab.config import (...)` block at the top of `main.py` imports `save_aliases` and `save_merchant_aliases`. With those deleted, the import would fail at module load time. Open `src/finab/main.py` and remove `save_aliases,` and `save_merchant_aliases,` from the import tuple. The remaining lines stay as-is.

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_config_shims.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite to confirm nothing else broke**

Run: `uv run pytest -v`
Expected: All tests pass.

If `test_hashing.py` or `test_sync_transactions.py` break due to the removed `save_*` functions, the breakage is in test fixtures that mock the now-deleted symbols. Update those mocks to drop the deleted-function references.

- [ ] **Step 6: Commit**

```bash
git add src/finab/config.py tests/test_config_shims.py
git commit -m "refactor(config): replace alias dicts with store-backed shims

load_aliases / load_merchant_aliases now synthesize the legacy flat dict
shape from ConfigStore. save_aliases / save_merchant_aliases are removed
since the store is the only write path going forward."
```

---

## Task 9: Helpers — `_normalize_alias` and `_prompt_alias_required`

**Files:**
- Modify: `src/finab/main.py`
- Create: `tests/test_helpers.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_helpers.py`:

```python
import unittest
from unittest.mock import patch
from io import StringIO

from finab.main import _normalize_alias, _prompt_alias_required


class TestNormalizeAlias(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(_normalize_alias("EASY EQUITIES"), "easy equities")

    def test_strips_whitespace(self):
        self.assertEqual(_normalize_alias("  Spar  "), "spar")

    def test_combined(self):
        self.assertEqual(_normalize_alias("  Easy EQUITIES  "), "easy equities")


class TestPromptAliasRequired(unittest.TestCase):
    @patch("builtins.input", return_value="Checking")
    def test_returns_input(self, mock_input):
        self.assertEqual(_prompt_alias_required("> "), "Checking")

    @patch("builtins.input", side_effect=["", "  ", "Finally"])
    @patch("sys.stdout", new_callable=StringIO)
    def test_reprompts_on_empty_or_whitespace(self, _stdout, _input):
        self.assertEqual(_prompt_alias_required("> "), "Finally")

    @patch("builtins.input", return_value="  Trimmed  ")
    def test_strips_whitespace_from_result(self, _input):
        self.assertEqual(_prompt_alias_required("> "), "Trimmed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_helpers.py -v`
Expected: FAIL — symbols don't exist in `main.py`.

- [ ] **Step 3: Add helpers to `src/finab/main.py`**

Insert near the other top-level helpers (around the `normalize_payee_for_matching` function):

```python
def _normalize_alias(alias: str) -> str:
    """Normalize an alias for lookup (lowercased, whitespace stripped).

    The stored alias keeps original casing; only index keys are normalized.
    """
    return alias.strip().lower()


def _prompt_alias_required(prompt: str, default: Optional[str] = None) -> str:
    """Prompt the user for an alias. Re-prompts until a non-empty value is entered."""
    while True:
        if default:
            shown = f"{prompt} (default: '{default}'): "
        else:
            shown = prompt
        raw = input(shown).strip()
        if raw:
            return raw
        if default:
            return default
        print("Alias is required. Please enter a value.")
```

Note: the test `test_reprompts_on_empty_or_whitespace` doesn't pass a `default`, so the `if default` branches don't fire — the function falls through to the print and re-prompts. The test `test_returns_input` and `test_strips_whitespace_from_result` also don't use `default`. The `default` path is exercised in Task 10/11 tests.

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_helpers.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/finab/main.py tests/test_helpers.py
git commit -m "feat(main): add _normalize_alias and _prompt_alias_required

Shared helpers for Phase 1 and Phase 2. Reject empty/whitespace
input via re-prompt loop."
```

---

## Task 10: New `sync_accounts`

**Files:**
- Modify: `src/finab/main.py`
- Create: `tests/test_sync_accounts.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_accounts.py`:

```python
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore


class TestSyncAccounts(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_account(self, id, name, type="depository", balance=100000):
        a = MagicMock()
        a.id = id
        a.name = name
        a.type = type
        a.balance = balance
        a.dict.return_value = {"id": id, "name": name, "type": type, "balance": balance}
        return a

    def _ynab_account(self, id, name):
        a = MagicMock()
        a.id = id
        a.ynab_id = id
        a.name = name
        a.to_dict.return_value = {"id": id, "name": name}
        return a

    @patch("finab.main.input", create=True, return_value="My Checking")
    def test_skips_already_linked_accounts(self, _input):
        # Pre-populate the store
        self.store.add_account(
            alias="My Checking",
            fw_record={"id": "fw-1", "name": "Checking"},
            ynab_record={"id": "yn-1", "name": "My Checking"},
        )
        # Reload to pick up indexes
        self.store = ConfigStore(self.config_path)

        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = [self._ynab_account("yn-1", "My Checking")]

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        # No new account created via API
        self.ynab_client.create_account.assert_not_called()

    @patch("finab.main.input", create=True, return_value="My Checking")
    def test_links_existing_ynab_when_name_matches_alias(self, _input):
        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = [self._ynab_account("yn-1", "My Checking")]
        self.fw_client.get_transactions.return_value = []  # for balance adjustment

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_account.assert_not_called()
        linked = self.store.account_by_finwise_id("fw-1")
        self.assertIsNotNone(linked)
        self.assertEqual(linked["alias"], "My Checking")
        self.assertEqual(linked["ynab"]["id"], "yn-1")

    @patch("finab.main.input", create=True, return_value="Brand New Account")
    def test_creates_ynab_account_when_no_match(self, _input):
        self.fw_client.get_accounts.return_value = [self._fw_account("fw-1", "Checking")]
        self.ynab_client.get_accounts.return_value = []  # nothing on YNAB side
        self.fw_client.get_transactions.return_value = []
        created = self._ynab_account("yn-NEW", "Brand New Account")
        self.ynab_client.create_account.return_value = MagicMock(data=MagicMock(account=created))

        from finab.main import sync_accounts
        sync_accounts(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_account.assert_called_once()
        linked = self.store.account_by_finwise_id("fw-1")
        self.assertIsNotNone(linked)
        self.assertEqual(linked["alias"], "Brand New Account")
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_sync_accounts.py -v`
Expected: FAIL — current `sync_accounts` has a different signature (no `store` arg) and uses the legacy alias dict.

- [ ] **Step 3: Replace `sync_accounts` in `src/finab/main.py`**

Delete the entire existing `sync_accounts` function (currently lines 70–198). Replace with:

```python
def sync_accounts(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
):
    """Phase 1: ensure every FinWise account has a matching entity in the store
    and a corresponding YNAB account, creating new YNAB accounts when needed."""
    print("\n--- Account Sync ---")

    try:
        fw_accounts = fw_client.get_accounts()
        print(f"FinWise Accounts: {len(fw_accounts)}")
    except Exception as e:
        print(f"Failed to fetch FinWise accounts: {e}")
        return

    try:
        ynab_accounts = ynab_client.get_accounts(budget_id)
        print(f"YNAB Accounts: {len(ynab_accounts)}")
    except Exception as e:
        print(f"Failed to fetch YNAB accounts: {e}")
        return

    store.refresh_records(fw_accounts=fw_accounts, ynab_accounts=ynab_accounts)

    ynab_by_name = {_normalize_alias(a.name): a for a in ynab_accounts}

    for fw_acc in fw_accounts:
        if store.account_by_finwise_id(fw_acc.id):
            continue

        alias = _prompt_alias_required(
            f"Enter YNAB account name for FinWise account '{fw_acc.name}'",
            default=fw_acc.name,
        )

        match = ynab_by_name.get(_normalize_alias(alias))
        if match:
            store.add_account(
                alias=alias,
                fw_record=to_dict(fw_acc),
                ynab_record=to_dict(match),
            )
            print(f"Linked '{fw_acc.name}' -> existing YNAB account '{match.name}'")
            continue

        # Create on YNAB side
        try:
            starting_balance = _calculate_starting_balance(fw_acc, fw_client)
            fw_acc_for_create = _account_with_overrides(
                fw_acc, name=alias, balance=starting_balance
            )
            response = ynab_client.create_account(budget_id, fw_acc_for_create)
            new_record = response.data.account
            store.add_account(
                alias=alias,
                fw_record=to_dict(fw_acc),
                ynab_record=to_dict(new_record),
            )
            print(f"Created YNAB account '{alias}'")
        except Exception as e:
            print(f"Failed to create YNAB account '{alias}': {e}")
            continue
```

Add these helpers near `_normalize_alias`:

```python
def _calculate_starting_balance(fw_acc, fw_client) -> int:
    """Reproduce today's balance-adjustment math: starting_balance =
    current_balance - sum(transactions since start of month)."""
    start_date = date.today().replace(day=1)
    try:
        txns = fw_client.get_transactions(start_date=start_date)
    except Exception:
        return fw_acc.balance
    account_txns = [t for t in txns if t.account_id == fw_acc.id]
    adjustment = sum(t.amount for t in account_txns)
    return int(fw_acc.balance - adjustment)


def _account_with_overrides(fw_acc, name: str, balance: int):
    """Return a shallow copy of fw_acc with name and balance overridden,
    suitable to pass to ynab_client.create_account."""
    # YNABClient.create_account treats the input as a finab.models.Account
    # (it reads .name, .type, .balance). Mutate a copy rather than the original.
    import copy
    copy_acc = copy.copy(fw_acc)
    copy_acc.name = name
    copy_acc.balance = balance
    return copy_acc
```

Add the imports to `main.py` near the top:

```python
from finab.store import ConfigStore, to_dict
```

The `sync_accounts` body above already uses `to_dict(...)` for converting FinWise / YNAB records to plain dicts before storing them.

- [ ] **Step 4: Run sync_accounts tests, expect pass**

Run: `uv run pytest tests/test_sync_accounts.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: All tests pass. If `test_hashing.py` or `test_sync_transactions.py` break, they likely reference the old `sync_accounts` signature or call the removed `save_aliases`. Update the test fixtures to remove those references (do NOT add new mocks for them — the symbols are gone for good).

- [ ] **Step 6: Commit**

```bash
git add src/finab/main.py tests/test_sync_accounts.py
git commit -m "feat(main): rewrite sync_accounts to use ConfigStore

Phase 1 of the new flow: iterate FinWise accounts, prompt for alias
when unknown, link to matching YNAB account or create one. Old code
that updated account_aliases is gone; the store is the single write
path. Balance-adjustment math is preserved as _calculate_starting_balance."
```

---

## Task 11: `sync_merchants` (Phase 2)

**Files:**
- Modify: `src/finab/main.py`
- Create: `tests/test_sync_merchants.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_sync_merchants.py`:

```python
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from finab.store import ConfigStore


class TestExtractDistinctMerchants(unittest.TestCase):
    def test_dedupes_by_merchant_id(self):
        from finab.main import _extract_distinct_merchants

        def fw_txn(mid, mname):
            t = MagicMock()
            t.merchant_id = mid
            t.merchant_name = mname
            return t

        txns = [
            fw_txn("m-1", "Spar"),
            fw_txn("m-1", "Spar"),
            fw_txn("m-2", "Checkers"),
            fw_txn(None, None),  # transactions with no merchant_id are skipped
        ]

        result = _extract_distinct_merchants(txns)
        ids = [m["id"] for m in result]
        self.assertEqual(sorted(ids), ["m-1", "m-2"])
        names = {m["id"]: m["name"] for m in result}
        self.assertEqual(names, {"m-1": "Spar", "m-2": "Checkers"})


class TestSyncMerchants(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.config_path)

        self.fw_client = MagicMock()
        self.ynab_client = MagicMock()

    def tearDown(self):
        self.tmpdir.cleanup()

    def _fw_txn(self, mid, mname):
        t = MagicMock()
        t.merchant_id = mid
        t.merchant_name = mname
        return t

    def _ynab_payee(self, id, name):
        p = MagicMock()
        p.id = id
        p.name = name
        p.to_dict.return_value = {"id": id, "name": name}
        return p

    @patch("finab.main.input", create=True, return_value="Spar")
    def test_attaches_second_finwise_to_existing_merchant(self, _input):
        # Existing merchant with one FinWise child
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar-1", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.config_path)

        # Transaction has a NEW FinWise merchant id with same alias-typed name
        self.fw_client.get_transactions.return_value = [
            self._fw_txn("fw-spar-2", "Spar"),
        ]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-spar", "Spar")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        # The new FinWise id is attached to the same merchant
        m = self.store.merchant_by_finwise_id("fw-spar-2")
        self.assertIsNotNone(m)
        self.assertEqual(m["alias"], "Spar")
        self.assertIn("fw-spar-1", m["finwise"])
        self.assertIn("fw-spar-2", m["finwise"])
        # No new YNAB payee created
        self.ynab_client.create_payee.assert_not_called()

    @patch("finab.main.input", create=True, return_value="New Payee")
    def test_creates_ynab_payee_when_no_match(self, _input):
        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-x", "X")]
        self.ynab_client.get_payees.return_value = []
        created = self._ynab_payee("yn-new", "New Payee")
        self.ynab_client.create_payee.return_value = created

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_payee.assert_called_once_with("bid", "New Payee")
        m = self.store.merchant_by_finwise_id("fw-x")
        self.assertEqual(m["alias"], "New Payee")
        self.assertEqual(m["ynab"]["id"], "yn-new")

    @patch("finab.main.input", create=True, return_value="Shell")
    def test_links_existing_ynab_payee_when_name_matches(self, _input):
        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-shell", "Shell")]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-shell", "Shell")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        self.ynab_client.create_payee.assert_not_called()
        m = self.store.merchant_by_finwise_id("fw-shell")
        self.assertEqual(m["ynab"]["id"], "yn-shell")

    @patch("finab.main.input", create=True, return_value="Spar")
    def test_skip_known_merchant(self, _input):
        # Already linked
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store = ConfigStore(self.config_path)

        self.fw_client.get_transactions.return_value = [self._fw_txn("fw-spar", "Spar")]
        self.ynab_client.get_payees.return_value = [self._ynab_payee("yn-spar", "Spar")]

        from finab.main import sync_merchants
        sync_merchants(self.fw_client, self.ynab_client, "bid", self.store)

        # No prompt should fire (mock_input would still have returned "Spar", but
        # we verify behavior by checking that create_payee wasn't called and the
        # merchant still has exactly one finwise child).
        self.ynab_client.create_payee.assert_not_called()
        m = self.store.merchant_by_finwise_id("fw-spar")
        self.assertEqual(len(m["finwise"]), 1)
```

- [ ] **Step 2: Run, expect fail**

Run: `uv run pytest tests/test_sync_merchants.py -v`
Expected: FAIL — `sync_merchants` and `_extract_distinct_merchants` don't exist.

- [ ] **Step 3: Add `_extract_distinct_merchants` and `sync_merchants` to `src/finab/main.py`**

```python
def _extract_distinct_merchants(fw_transactions) -> list[dict]:
    """Walk FinWise transactions and emit one record per unique merchant_id.

    FinWise has no merchant endpoint; merchant data lives on transactions.
    """
    seen: dict[str, dict] = {}
    for t in fw_transactions:
        mid = getattr(t, "merchant_id", None)
        if not mid:
            continue
        if mid in seen:
            continue
        seen[mid] = {
            "id": mid,
            "name": getattr(t, "merchant_name", None) or mid,
        }
    return list(seen.values())


def sync_merchants(
    fw_client: FinWiseClient,
    ynab_client: YNABClient,
    budget_id: str,
    store: ConfigStore,
):
    """Phase 2: ensure every distinct FinWise merchant has a matching entity
    in the store and a corresponding YNAB payee, creating new YNAB payees
    when needed."""
    print("\n--- Merchant Sync ---")

    start_date = date.today().replace(day=1)

    try:
        fw_transactions = fw_client.get_transactions(start_date=start_date)
    except Exception as e:
        print(f"Failed to fetch FinWise transactions: {e}")
        return

    try:
        ynab_payees = ynab_client.get_payees(budget_id)
    except Exception as e:
        print(f"Failed to fetch YNAB payees: {e}")
        return

    store.refresh_records(ynab_payees=ynab_payees)

    fw_merchants = _extract_distinct_merchants(fw_transactions)
    print(f"Distinct FinWise merchants in period: {len(fw_merchants)}")

    ynab_by_name = {_normalize_alias(p.name): p for p in ynab_payees}

    for fw_m in fw_merchants:
        if store.merchant_by_finwise_id(fw_m["id"]):
            continue

        alias = _prompt_alias_required(
            f"Enter YNAB payee for merchant '{fw_m['name']}' (id={fw_m['id']})",
            default=fw_m["name"],
        )

        existing = store.merchant_by_alias(alias)
        if existing:
            store.attach_finwise_to_merchant(existing["id"], fw_m)
            print(
                f"Attached FinWise merchant '{fw_m['name']}' to existing "
                f"'{existing['alias']}'"
            )
            continue

        ynab_match = ynab_by_name.get(_normalize_alias(alias))
        if ynab_match:
            store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(ynab_match),
            )
            print(f"Linked merchant '{alias}' -> existing YNAB payee")
            continue

        try:
            new_payee = ynab_client.create_payee(budget_id, alias)
            store.add_merchant(
                alias=alias,
                fw_record=fw_m,
                ynab_record=to_dict(new_payee),
            )
            print(f"Created YNAB payee '{alias}'")
        except Exception as e:
            print(f"Failed to create YNAB payee '{alias}': {e}")
            continue
```

- [ ] **Step 4: Run, expect pass**

Run: `uv run pytest tests/test_sync_merchants.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/finab/main.py tests/test_sync_merchants.py
git commit -m "feat(main): add sync_merchants for Phase 2

Iterates distinct FinWise merchants pulled from transactions. Prompts
for alias; attaches to an existing merchant (multi-FinWise-id case),
links to an existing YNAB payee, or creates a new payee via
YNABClient.create_payee. One prompt per distinct merchant per run."
```

---

## Task 12: Wire `main()` to instantiate `ConfigStore` and call both syncs

**Files:**
- Modify: `src/finab/main.py`

- [ ] **Step 1: Update the `main()` function**

Locate the existing `main()` function (currently around line 1184). Find these lines near the bottom of `main()`:

```python
if budget_id:
    # Sync Accounts
    sync_accounts(fw_client, ynab_client, budget_id)

    # Sync Transactions
    sync_transactions(fw_client, ynab_client, budget_id)
```

Replace with:

```python
if budget_id:
    store = ConfigStore()

    # Phase 1
    sync_accounts(fw_client, ynab_client, budget_id, store)

    # Phase 2
    sync_merchants(fw_client, ynab_client, budget_id, store)

    # Phase 3 (existing transaction pipeline, unchanged)
    sync_transactions(fw_client, ynab_client, budget_id)
```

- [ ] **Step 2: Run the full test suite to catch any wiring breakage**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 3: Commit**

```bash
git add src/finab/main.py
git commit -m "feat(main): wire ConfigStore and Phase 2 into main()

main() now constructs a ConfigStore once and passes it to sync_accounts
and the new sync_merchants. The existing sync_transactions remains
untouched and reads the store via config.py compat shims."
```

---

## Task 13: Convert in-pipeline `save_merchant_aliases` callsites

**Files:**
- Modify: `src/finab/main.py`

The existing `process_payee_aliases` and `process_categories` write to `merchant_aliases` whenever the user inputs a new merchant alias mid-transaction. After Phase 2, those branches are unreachable in practice, but they remain in code as defensive fallbacks. They need to call the store instead of the now-deleted `save_merchant_aliases`.

- [ ] **Step 1: Grep for the deleted symbol to find every callsite**

Run: `grep -n "save_merchant_aliases\|merchant_aliases\[" src/finab/main.py`

Expected: 4 callsites in `process_payee_aliases` and `process_categories`. Each one currently does:

```python
merchant_aliases[fw_txn.merchant_id] = target
save_merchant_aliases(merchant_aliases)
```

- [ ] **Step 2: Replace each callsite with a store-aware helper**

Add this helper near the other top-level helpers in `src/finab/main.py`:

```python
def _record_merchant_alias(store: ConfigStore, fw_merchant_id: str, alias: str, fw_merchant_name: Optional[str] = None) -> None:
    """Defensive fallback used by the transaction pipeline when a merchant id
    appears that wasn't covered by Phase 2. Attaches to an existing merchant
    matching `alias`, or creates a new merchant with a placeholder ynab record."""
    fw_record = {"id": fw_merchant_id, "name": fw_merchant_name or fw_merchant_id}

    existing = store.merchant_by_alias(alias)
    if existing:
        store.attach_finwise_to_merchant(existing["id"], fw_record)
        return
    # No existing merchant; create one with an empty ynab record. Phase 2
    # on the next run will pick this up if a matching YNAB payee exists.
    store.add_merchant(alias=alias, fw_record=fw_record, ynab_record={})
```

Now convert each of the 4 callsites. The current pattern in `process_payee_aliases` and `process_categories`:

```python
merchant_aliases[fw_txn.merchant_id] = target
merchant_aliases_modified = True
save_merchant_aliases(merchant_aliases)
```

(or the `process_categories` variant that loads, mutates, saves)

Becomes:

```python
_record_merchant_alias(
    store,
    fw_merchant_id=fw_txn.merchant_id,
    alias=target,
    fw_merchant_name=getattr(fw_txn, "merchant_name", None),
)
```

To pass `store` through, update the signatures:

- `process_payee_aliases(fw_transactions, ynab_client, budget_id, account_id_to_name, ynab_accounts)` → add `store: ConfigStore` as the last positional parameter.
- `process_categories(transactions_to_process, ynab_client, budget_id, account_id_to_name, ynab_accounts)` → add `store: ConfigStore` as the last positional parameter.
- `sync_transactions(finwise_client, ynab_client, budget_id)` → add `store: ConfigStore` as last positional parameter, and forward it to both `process_payee_aliases` and `process_categories`.
- `main()` callsite (just edited in Task 12): change `sync_transactions(fw_client, ynab_client, budget_id)` → `sync_transactions(fw_client, ynab_client, budget_id, store)`.

Inside `process_payee_aliases`, the existing local `merchant_aliases = load_merchant_aliases()` should now read from the store-backed shim. Pass the store through to avoid re-reading: replace `merchant_aliases = load_merchant_aliases()` with `merchant_aliases = load_merchant_aliases(store=store)`.

Same for any `load_merchant_aliases()` calls inside `process_categories`.

Delete every `save_merchant_aliases(...)` line and every `merchant_aliases_modified = True` variable assignment + the trailing `if merchant_aliases_modified: save_merchant_aliases(merchant_aliases)` blocks at the bottom of `process_payee_aliases`.

- [ ] **Step 3: Run the full test suite**

Run: `uv run pytest -v`
Expected: All tests pass. If `test_sync_transactions.py` breaks because of the new signature, update it to pass a `ConfigStore` (instantiated against a `tmp_path`-based file).

- [ ] **Step 4: Verify no callers of removed symbols remain**

Run: `grep -rn "save_merchant_aliases\|save_aliases" src/ tests/`
Expected: zero matches (the only matches should be in `tests/test_config_shims.py::test_save_aliases_no_longer_exists` which asserts on absence).

- [ ] **Step 5: Commit**

```bash
git add src/finab/main.py tests/
git commit -m "refactor(main): route in-pipeline merchant writes through ConfigStore

The 4 callsites in process_payee_aliases / process_categories that
used to write to merchant_aliases now go through _record_merchant_alias,
which attaches to an existing merchant by alias or creates a new one.
After Phase 2 these branches are unreachable in practice, but kept as
defensive fallbacks."
```

---

## Task 14: Manual verification checklist

**Files:** None (manual run against real APIs).

This task does not produce a commit. It exists to surface the three integration risks flagged in the spec's "Open Questions for Implementation" section. Run it after Task 13 lands.

- [ ] **Step 1: Back up real `config.json`**

```bash
cp config.json config.json.preflight.bak
```

- [ ] **Step 2: Delete `accounts` and `merchants` from `config.json` (fresh start)**

If they exist from any prior testing, edit `config.json` to remove the `accounts` and `merchants` top-level keys. `budget_id`, `payee_rules`, `categories`, and `import_id_offset` must remain. `account_aliases` and `merchant_aliases` can stay as dead data; the new code ignores them.

- [ ] **Step 3: Run the app and walk through Phase 1**

Run: `uv run finab`

You should be prompted once per FinWise account. Verify:
- Pressing Enter accepts the default (FinWise account name).
- Empty + Enter without a default re-prompts.
- For a name that already exists in YNAB, no API create call fires (watch the printed log).
- For a name that doesn't exist in YNAB, the account is created and a starting-balance value is shown. **Verify the sign for credit/loan accounts** — if you have one, confirm the starting balance is correct in YNAB after creation (this is open question #2 from the spec).

- [ ] **Step 4: Walk through Phase 2**

Continuing the same run, you should be prompted once per distinct FinWise merchant. Verify:
- Typing an alias that matches an existing YNAB payee (e.g., something you already have) links without creating a new payee.
- Typing the same alias for a second prompt attaches to the existing merchant rather than creating a duplicate.
- Typing a brand-new alias triggers `YNABClient.create_payee`. After the run, check YNAB's payee list to confirm the payee actually exists.

- [ ] **Step 5: Inspect the resulting `config.json`**

```bash
jq '.accounts | length, .merchants | length' config.json
```

Verify the counts roughly match what you expect.

```bash
jq '.merchants | to_entries | map(select(.value.finwise | keys | length > 1)) | length' config.json
```

For any merchants you intentionally consolidated (e.g., the two "Easy Equities" FinWise IDs), this should be ≥1.

- [ ] **Step 6: Re-run the app**

Run: `uv run finab`

Verify:
- No account prompts (everything already linked).
- No merchant prompts (everything already linked).
- The transaction pipeline runs and produces the same output it did before this refactor for already-seen transactions.

- [ ] **Step 7: Resolve open questions if surfaced**

If any of the three "Open Questions for Implementation" from the spec produced unexpected behavior (account currency handling, credit/loan starting balance sign, FinWise merchant field shape), open a follow-up issue or fix inline before declaring the work done.

---

## Summary of Commits

After all tasks, the branch should have 12 atomic commits (Task 14 is verification only):

1. `build: swap ynab-api for official ynab SDK`
2. `refactor(ynab): migrate YNABClient to official ynab SDK`
3. `feat(ynab): add YNABClient.create_payee`
4. `feat(store): scaffold ConfigStore with load, save, indexes`
5. `feat(store): account add + lookup by finwise id`
6. `feat(store): merchant add, attach, lookup by id and alias`
7. `feat(store): refresh_records keeps nested records fresh each run`
8. `refactor(config): replace alias dicts with store-backed shims`
9. `feat(main): add _normalize_alias and _prompt_alias_required`
10. `feat(main): rewrite sync_accounts to use ConfigStore`
11. `feat(main): add sync_merchants for Phase 2`
12. `feat(main): wire ConfigStore and Phase 2 into main()`
13. `refactor(main): route in-pipeline merchant writes through ConfigStore`

The above ordering is safe to bisect: every commit leaves the test suite green and the app functional.
