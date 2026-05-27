import unittest

from finab.store import normalize_alias


class TestNormalizeAlias(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(normalize_alias("EASY EQUITIES"), "easy equities")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_alias("  Spar  "), "spar")

    def test_combined(self):
        self.assertEqual(normalize_alias("  Easy EQUITIES  "), "easy equities")


if __name__ == "__main__":
    unittest.main()
