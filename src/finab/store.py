import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional


CONFIG_FILE = Path("config.json")
ACCOUNTS_FILE = Path("accounts.json")
MERCHANTS_FILE = Path("merchants.json")


def normalize_alias(alias: str) -> str:
    return alias.strip().lower()


def to_dict(obj) -> dict:
    """Convert a pydantic-like or dataclass-like object to a plain dict.

    For pydantic v2 models, uses `mode="json"` so UUID / datetime / Decimal
    fields come out as JSON-safe primitives (strings, etc.) — matching what
    they'd look like after a round-trip through `json.dump`.
    """
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump(mode="json")
        except TypeError:
            return obj.model_dump()
    for method_name in ("to_dict", "dict"):
        method = getattr(obj, method_name, None)
        if callable(method):
            return method()
    # Fall back to __dict__ (dataclasses, simple objects)
    return dict(obj.__dict__)


def _load_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _write_file(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4, default=str)
    os.replace(tmp, path)


class ConfigStore:
    def __init__(
        self,
        path: Optional[Path] = None,
        accounts_path: Optional[Path] = None,
        merchants_path: Optional[Path] = None,
    ):
        # Resolve defaults lazily so tests (conftest) can monkey-patch
        # module-level constants before any ConfigStore() is constructed.
        explicit_path = path is not None
        if path is None:
            path = CONFIG_FILE
        self.path = Path(path)

        if accounts_path is None:
            accounts_path = (
                self.path.parent / "accounts.json"
                if explicit_path
                else ACCOUNTS_FILE
            )
        self.accounts_path = Path(accounts_path)

        if merchants_path is None:
            merchants_path = (
                self.path.parent / "merchants.json"
                if explicit_path
                else MERCHANTS_FILE
            )
        self.merchants_path = Path(merchants_path)

        config_data = _load_file(self.path)
        accounts_data = _load_file(self.accounts_path)
        merchants_data = _load_file(self.merchants_path)

        # One-time migration: 'accounts' key in config.json → accounts.json
        if "accounts" in config_data:
            migrated = config_data.pop("accounts")
            if not accounts_data.get("accounts"):
                accounts_data = {"accounts": migrated}
            _write_file(self.accounts_path, accounts_data)
            _write_file(self.path, config_data)

        # One-time migration: 'merchants' key in config.json → merchants.json
        if "merchants" in config_data:
            migrated = config_data.pop("merchants")
            if not merchants_data.get("merchants"):
                merchants_data = {"merchants": migrated}
            _write_file(self.merchants_path, merchants_data)
            _write_file(self.path, config_data)

        self._data: dict = config_data
        self._data["accounts"] = accounts_data.get("accounts", {})
        self._data["merchants"] = merchants_data.get("merchants", {})
        self._rebuild_indexes()

    def _save(self) -> None:
        accounts = self._data.get("accounts", {})
        merchants = self._data.get("merchants", {})
        config_portion = {
            k: v for k, v in self._data.items()
            if k not in ("accounts", "merchants")
        }
        _write_file(self.path, config_portion)
        _write_file(self.accounts_path, {"accounts": accounts})
        _write_file(self.merchants_path, {"merchants": merchants})

    def _rebuild_indexes(self) -> None:
        # One-shot migration: legacy merchant.last_processing -> processings.
        migrated_any = False
        for m in self._data.get("merchants", {}).values():
            if "last_processing" in m and "processings" not in m:
                lp = m.pop("last_processing")
                key = str(lp.get("amount_milliunits"))
                m["processings"] = {
                    key: {
                        "parent_memo": lp.get("parent_memo", ""),
                        "splits": lp.get("splits", []),
                    }
                }
                migrated_any = True

        self._fw_account_index: dict[str, str] = {}
        self._alias_account_index: dict[str, str] = {}
        self._ynab_account_index: dict[str, str] = {}
        self._fw_merchant_index: dict[str, str] = {}
        self._alias_merchant_index: dict[str, str] = {}

        for acc in self._data.get("accounts", {}).values():
            fw = acc.get("finwise", {})
            if fw.get("id"):
                self._fw_account_index[fw["id"]] = acc["id"]
            if acc.get("alias"):
                self._alias_account_index[normalize_alias(acc["alias"])] = acc["id"]
            yn_id = acc.get("ynab", {}).get("id")
            if yn_id:
                self._ynab_account_index[str(yn_id)] = acc["id"]

        for m in self._data.get("merchants", {}).values():
            for fw_id in m.get("finwise", {}):
                self._fw_merchant_index[fw_id] = m["id"]
            if m.get("alias"):
                self._alias_merchant_index[normalize_alias(m["alias"])] = m["id"]

        if migrated_any:
            self._save()

    def set_budget_id(self, budget_id: str) -> None:
        """Persist the YNAB budget id into config.json.

        budget_id lives in the same `_data` dict as accounts/merchants, so
        writing it through the store (rather than the standalone
        config.save_budget_id) keeps the in-memory state coherent — otherwise
        a later account/merchant save would re-emit `_data` and clobber a
        budget_id written behind the store's back. Read back at startup via
        config.load_budget_id.
        """
        self._data["budget_id"] = budget_id
        self._save()

    def accounts(self) -> Iterable[dict]:
        return self._data["accounts"].values()

    def merchants(self) -> Iterable[dict]:
        return self._data["merchants"].values()

    def add_account(
        self,
        alias: str,
        fw_record: dict,
        ynab_record: dict,
        ignore_transactions: bool = False,
    ) -> dict:
        internal_id = str(uuid.uuid4())
        account = {
            "id": internal_id,
            "alias": alias,
            "finwise": dict(fw_record),
            "ynab": dict(ynab_record),
            "ignore_transactions": bool(ignore_transactions),
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

    def account_by_alias(self, alias: str) -> Optional[dict]:
        internal_id = self._alias_account_index.get(normalize_alias(alias))
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]

    def account_by_ynab_id(self, ynab_id: str) -> Optional[dict]:
        internal_id = self._ynab_account_index.get(str(ynab_id))
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]

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
        internal_id = self._alias_merchant_index.get(normalize_alias(alias))
        if not internal_id:
            return None
        return self._data["merchants"][internal_id]

    def set_account_ynab_record(self, account_id: str, ynab_record: dict) -> None:
        """Replace an account entry's YNAB sub-record (used by the reconcile
        step when a YNAB account is created/recreated to match the store)."""
        acc = self._data["accounts"][account_id]
        acc["ynab"] = dict(ynab_record)
        self._rebuild_indexes()
        self._save()

    def set_merchant_ynab_record(self, merchant_id: str, ynab_record: dict) -> None:
        """Replace a merchant entry's YNAB sub-record."""
        m = self._data["merchants"][merchant_id]
        m["ynab"] = dict(ynab_record)
        self._rebuild_indexes()
        self._save()

    def set_merchant_memory(
        self,
        merchant_id: str,
        categories_used: dict,
        processings: dict,
    ) -> None:
        """Write the per-merchant categorization memory atomically.

        `processings` is a dict keyed by str(amount_milliunits) holding
        {parent_memo, splits} entries — one per distinct amount this
        merchant has been categorized for.
        """
        m = self._data["merchants"][merchant_id]
        m["categories_used"] = dict(categories_used)
        m["processings"] = dict(processings)
        self._rebuild_indexes()
        self._save()

    def set_account_alias(self, account_id: str, alias: str) -> None:
        """Rename an account's alias. Rebuilds the alias index."""
        acc = self._data["accounts"][account_id]
        acc["alias"] = alias
        self._rebuild_indexes()
        self._save()

    def set_account_ignore(self, account_id: str, ignore: bool) -> None:
        """Toggle whether transactions on this account are processed."""
        acc = self._data["accounts"][account_id]
        acc["ignore_transactions"] = bool(ignore)
        self._save()

    def set_merchant_alias(self, merchant_id: str, alias: str) -> None:
        """Rename a merchant's alias. Rebuilds the alias index."""
        m = self._data["merchants"][merchant_id]
        m["alias"] = alias
        self._rebuild_indexes()
        self._save()

    def delete_processing_entry(self, merchant_id: str, amount_key: str) -> None:
        """Drop a single entry from a merchant's processings dict.

        Idempotent — silently no-ops if the key isn't present. Does NOT
        adjust `categories_used` counts; those are statistical and
        shouldn't change just because one historical entry was forgotten.
        """
        m = self._data["merchants"][merchant_id]
        processings = m.get("processings") or {}
        if amount_key in processings:
            del processings[amount_key]
            m["processings"] = processings
            self._save()

    def reset_merchant_memory(self, merchant_id: str) -> None:
        """Wipe both categories_used and processings on a merchant. The
        merchant entry itself (alias, FW/YNAB linkage) is preserved.
        """
        m = self._data["merchants"][merchant_id]
        m["categories_used"] = {}
        m["processings"] = {}
        self._save()

    def refresh_records(
        self,
        fw_accounts=None,
        ynab_accounts=None,
        ynab_payees=None,
    ) -> None:
        """Overwrite cached finwise/ynab sub-records with freshly fetched data.

        Stored sub-records use a curated shape: `{"id": <fw_id_or_yn_id>,
        "name": ..., ...}`. The internal Account model carries the FinWise id
        as `finwise_id` and the YNAB id as `ynab_id`, so we rebuild the
        curated dicts explicitly rather than blindly serializing the whole
        model (which would produce the wrong key names).
        """
        changed = False

        if fw_accounts:
            for fw in fw_accounts:
                fw_id = getattr(fw, "finwise_id", None) or getattr(fw, "id", None)
                if not fw_id:
                    continue
                acc = self.account_by_finwise_id(fw_id)
                if acc:
                    acc["finwise"] = {
                        "id": fw_id,
                        "name": getattr(fw, "name", None),
                        "type": getattr(fw, "type", None),
                        "balance": getattr(fw, "balance", None),
                        "currency_code": getattr(fw, "currency_code", None),
                    }
                    changed = True

        if ynab_accounts:
            yn_by_id: dict[str, Any] = {}
            for y in ynab_accounts:
                yid = getattr(y, "ynab_id", None) or getattr(y, "id", None)
                if yid is not None:
                    yn_by_id[str(yid)] = y
            for acc in self.accounts():
                yn_id = acc["ynab"].get("id")
                if yn_id and str(yn_id) in yn_by_id:
                    y = yn_by_id[str(yn_id)]
                    acc["ynab"] = {
                        "id": str(getattr(y, "ynab_id", None) or getattr(y, "id", "")),
                        "name": getattr(y, "name", None),
                        "type": getattr(y, "type", None),
                        "balance": getattr(y, "balance", None),
                        "transfer_payee_id": (
                            str(y.transfer_payee_id)
                            if getattr(y, "transfer_payee_id", None) is not None
                            else None
                        ),
                    }
                    changed = True

        if ynab_payees:
            yn_by_id = {str(p.id): p for p in ynab_payees if getattr(p, "id", None) is not None}
            for m in self.merchants():
                yn_id = m["ynab"].get("id")
                if yn_id and str(yn_id) in yn_by_id:
                    m["ynab"] = to_dict(yn_by_id[str(yn_id)])
                    changed = True

        if changed:
            self._save()
