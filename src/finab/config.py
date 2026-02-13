import json
from pathlib import Path
from typing import Dict, List, Any, Optional

CONFIG_FILE = Path("config.json")


def _load_data() -> Dict[str, Any]:
    """Helper to load all data from config.json."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except json.JSONDecodeError:
        return {}


def _save_data(data: Dict[str, Any]) -> None:
    """Helper to save all data to config.json."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=4)


def load_aliases() -> Dict[str, str]:
    """Loads the account aliases from config.json."""
    data = _load_data()
    return data.get("account_aliases", {})


def save_aliases(aliases: Dict[str, str]) -> None:
    """Saves the account aliases to config.json."""
    data = _load_data()
    data["account_aliases"] = aliases
    _save_data(data)


def load_payee_rules() -> List[Dict[str, str]]:
    """Loads the payee regex rules from config.json."""
    data = _load_data()
    return data.get("payee_rules", [])


def save_payee_rules(rules: List[Dict[str, str]]) -> None:
    """Saves the payee regex rules to config.json."""
    data = _load_data()
    data["payee_rules"] = rules
    _save_data(data)


def load_category_rules() -> Dict[str, str]:
    """Loads the category regex rules from config.json."""
    data = _load_data()
    return data.get("categories", {})


def save_category_rules(rules: Dict[str, str]) -> None:
    """Saves the category regex rules to config.json."""
    data = _load_data()
    data["categories"] = rules
    _save_data(data)


def load_merchant_aliases() -> Dict[str, str]:
    """Loads the merchant aliases from config.json."""
    data = _load_data()
    return data.get("merchant_aliases", {})


def save_merchant_aliases(aliases: Dict[str, str]) -> None:
    """Saves the merchant aliases to config.json."""
    data = _load_data()
    data["merchant_aliases"] = aliases
    _save_data(data)


def load_budget_id() -> Optional[str]:
    """Loads the stored budget ID from config.json."""
    data = _load_data()
    return data.get("budget_id")


def save_budget_id(budget_id: str) -> None:
    """Saves the budget ID to config.json."""
    data = _load_data()
    data["budget_id"] = budget_id
    _save_data(data)


def load_import_id_offset() -> str:
    """Loads the transaction import ID offset from config.json.

    Checks for 'import_id_offset' first, then falls back to legacy 'salt' key,
    and defaults to 'finab_offset_v1' if neither exists.
    """
    data = _load_data()
    if "import_id_offset" in data:
        return data["import_id_offset"]
    if "salt" in data:
        return data["salt"]
    return "finab_offset_v1"


def save_import_id_offset(offset: str) -> None:
    """Saves the import ID offset to config.json.

    Saves under 'import_id_offset' and removes legacy 'salt' key if present.
    """
    data = _load_data()
    data["import_id_offset"] = offset
    if "salt" in data:
        del data["salt"]
    _save_data(data)
