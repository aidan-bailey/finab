import json
import os
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional


CONFIG_FILE = Path("config.json")


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
        self._ynab_account_index: dict[str, str] = {}
        self._alias_account_index: dict[str, str] = {}
        self._fw_merchant_index: dict[str, str] = {}
        self._ynab_merchant_index: dict[str, str] = {}
        self._alias_merchant_index: dict[str, str] = {}

        for acc in self._data.get("accounts", {}).values():
            fw = acc.get("finwise", {})
            if fw.get("id"):
                self._fw_account_index[fw["id"]] = acc["id"]
            yn = acc.get("ynab", {})
            if yn.get("id"):
                self._ynab_account_index[str(yn["id"])] = acc["id"]
            if acc.get("alias"):
                self._alias_account_index[normalize_alias(acc["alias"])] = acc["id"]

        for m in self._data.get("merchants", {}).values():
            for fw_id in m.get("finwise", {}):
                self._fw_merchant_index[fw_id] = m["id"]
            yn = m.get("ynab", {})
            if yn.get("id"):
                self._ynab_merchant_index[str(yn["id"])] = m["id"]
            if m.get("alias"):
                self._alias_merchant_index[normalize_alias(m["alias"])] = m["id"]

    def accounts(self) -> Iterable[dict]:
        return self._data["accounts"].values()

    def merchants(self) -> Iterable[dict]:
        return self._data["merchants"].values()

    def add_account(self, alias: str, fw_record: dict, ynab_record: dict) -> dict:
        """Create a new account entry. `fw_record` may be {} when seeding from
        the YNAB side; in that case the account has no FinWise counterpart yet
        and `attach_finwise_to_account` can be used later."""
        internal_id = str(uuid.uuid4())
        account = {
            "id": internal_id,
            "alias": alias,
            "finwise": dict(fw_record) if fw_record else {},
            "ynab": dict(ynab_record),
        }
        self._data["accounts"][internal_id] = account
        self._rebuild_indexes()
        self._save()
        return account

    def attach_finwise_to_account(self, account_id: str, fw_record: dict) -> None:
        account = self._data["accounts"][account_id]
        account["finwise"] = dict(fw_record)
        self._rebuild_indexes()
        self._save()

    def account_by_finwise_id(self, fw_id: str) -> Optional[dict]:
        internal_id = self._fw_account_index.get(fw_id)
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]

    def account_by_ynab_id(self, ynab_id: str) -> Optional[dict]:
        internal_id = self._ynab_account_index.get(str(ynab_id))
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]

    def account_by_alias(self, alias: str) -> Optional[dict]:
        internal_id = self._alias_account_index.get(normalize_alias(alias))
        if not internal_id:
            return None
        return self._data["accounts"][internal_id]

    def add_merchant(self, alias: str, fw_record: dict, ynab_record: dict) -> dict:
        """Create a new merchant entry. `fw_record` may be {} when seeding from
        the YNAB side; in that case the merchant has no FinWise children yet
        and `attach_finwise_to_merchant` can be used later."""
        internal_id = str(uuid.uuid4())
        merchant = {
            "id": internal_id,
            "alias": alias,
            "finwise": {},
            "ynab": dict(ynab_record),
        }
        if fw_record:
            merchant["finwise"][fw_record["id"]] = dict(fw_record)
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

    def merchant_by_ynab_id(self, ynab_id: str) -> Optional[dict]:
        internal_id = self._ynab_merchant_index.get(str(ynab_id))
        if not internal_id:
            return None
        return self._data["merchants"][internal_id]

    def merchant_by_alias(self, alias: str) -> Optional[dict]:
        internal_id = self._alias_merchant_index.get(normalize_alias(alias))
        if not internal_id:
            return None
        return self._data["merchants"][internal_id]

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
