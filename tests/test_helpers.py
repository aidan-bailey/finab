import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from io import StringIO

from finab.main import (
    normalize_alias,
    _prompt_alias_required,
    _prompt_alias_with_picker,
)
from finab.store import ConfigStore


class TestNormalizeAlias(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(normalize_alias("EASY EQUITIES"), "easy equities")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_alias("  Spar  "), "spar")

    def test_combined(self):
        self.assertEqual(normalize_alias("  Easy EQUITIES  "), "easy equities")


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


class TestPromptAliasWithPicker(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "config.json"
        self.store = ConfigStore(self.path)
        # Seed: 2 merchants and 1 account, sorted alphabetically:
        #   1. [m] Apple    2. [m] Spar    3. [a] Checking
        self.store.add_merchant(
            alias="Spar",
            fw_record={"id": "fw-spar", "name": "Spar"},
            ynab_record={"id": "yn-spar", "name": "Spar"},
        )
        self.store.add_merchant(
            alias="Apple",
            fw_record={"id": "fw-apple", "name": "Apple"},
            ynab_record={"id": "yn-apple", "name": "Apple"},
        )
        self.store.add_account(
            alias="Checking",
            fw_record={"id": "fw-chk", "name": "Checking"},
            ynab_record={"id": "yn-chk", "transfer_payee_id": "tp-chk"},
        )
        self.store = ConfigStore(self.path)  # rebuild indexes

    def tearDown(self):
        self.tmpdir.cleanup()

    @patch("sys.stdout", new_callable=StringIO)
    @patch("builtins.input", side_effect=["?", "1"])
    def test_picker_returns_alias_for_numbered_choice(self, _input, _stdout):
        # Picks "Apple" (alphabetically first merchant)
        self.assertEqual(_prompt_alias_with_picker("> ", self.store), "Apple")

    @patch("sys.stdout", new_callable=StringIO)
    @patch("builtins.input", side_effect=["?", "3"])
    def test_picker_can_choose_account(self, _input, _stdout):
        self.assertEqual(_prompt_alias_with_picker("> ", self.store), "Checking")

    @patch("sys.stdout", new_callable=StringIO)
    @patch("builtins.input", side_effect=["?", "", "Fresh Name"])
    def test_picker_back_then_freeform(self, _input, _stdout):
        # Enter list, press Enter to back out, then type a fresh name.
        self.assertEqual(_prompt_alias_with_picker("> ", self.store), "Fresh Name")

    @patch("sys.stdout", new_callable=StringIO)
    @patch("builtins.input", side_effect=["?", "99", "2"])
    def test_picker_rejects_out_of_range(self, _input, _stdout):
        # 99 is out of range; reprompt; then pick 2 (Spar).
        self.assertEqual(_prompt_alias_with_picker("> ", self.store), "Spar")

    @patch("builtins.input", return_value="Direct")
    def test_no_picker_when_user_types_alias(self, _input):
        self.assertEqual(_prompt_alias_with_picker("> ", self.store), "Direct")


if __name__ == "__main__":
    unittest.main()
