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


def load_budget_id() -> Optional[str]:
    """Loads the stored budget ID from config.json."""
    data = _load_data()
    return data.get("budget_id")


def save_budget_id(budget_id: str) -> None:
    """Saves the budget ID to config.json."""
    data = _load_data()
    data["budget_id"] = budget_id
    _save_data(data)


def load_salt() -> str:
    """Loads the transaction import ID salt from config.json."""
    data = _load_data()
    return data.get("salt", "_rev7")  # Default to existing if not found


def save_salt(salt: str) -> None:
    """Saves the transaction import ID salt to config.json."""
    data = _load_data()
    data["salt"] = salt
    _save_data(data)
