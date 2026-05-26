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
