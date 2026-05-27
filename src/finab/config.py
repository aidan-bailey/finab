import json
from pathlib import Path
from typing import Dict, Any, Optional
from finab.store import ConfigStore

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
        json.dump(data, f, indent=4, default=str)


def load_budget_id() -> Optional[str]:
    """Loads the stored budget ID from config.json."""
    data = _load_data()
    return data.get("budget_id")


def save_budget_id(budget_id: str) -> None:
    """Saves the budget ID to config.json."""
    data = _load_data()
    data["budget_id"] = budget_id
    _save_data(data)


