import json
import os
import uuid
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
