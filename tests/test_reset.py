"""Tests for `finab --reset` (full reset of all four state files).

Each test re-points the canonical path constants at a per-test tmp_path so
`run_reset` (which resolves them lazily at call time) operates on throwaway
files rather than the developer's real config.json / accounts.json /
merchants.json / transactions.json.
"""
import finab.store as store_mod
import finab.transactions as transactions_mod
from finab.main import run_reset


def _point_state_at(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    accts = tmp_path / "accounts.json"
    merchants = tmp_path / "merchants.json"
    txn = tmp_path / "transactions.json"
    monkeypatch.setattr(store_mod, "CONFIG_FILE", cfg)
    monkeypatch.setattr(store_mod, "ACCOUNTS_FILE", accts)
    monkeypatch.setattr(store_mod, "MERCHANTS_FILE", merchants)
    monkeypatch.setattr(transactions_mod, "TRANSACTIONS_FILE", txn)
    return cfg, accts, merchants, txn


def test_confirm_deletes_all_state_files(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    cfg.write_text("{}")
    accts.write_text("{}")
    merchants.write_text("{}")
    txn.write_text("{}")

    result = run_reset(input_fn=lambda _: "y", output_fn=lambda *_: None)

    assert result is True
    assert not cfg.exists()
    assert not accts.exists()
    assert not merchants.exists()
    assert not txn.exists()


def test_cancel_keeps_files(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    cfg.write_text("{}")
    accts.write_text("{}")
    merchants.write_text("{}")
    txn.write_text("{}")

    result = run_reset(input_fn=lambda _: "n", output_fn=lambda *_: None)

    assert result is False
    assert cfg.exists()
    assert accts.exists()
    assert merchants.exists()
    assert txn.exists()


def test_empty_answer_cancels(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    cfg.write_text("{}")
    accts.write_text("{}")
    merchants.write_text("{}")
    txn.write_text("{}")

    result = run_reset(input_fn=lambda _: "", output_fn=lambda *_: None)

    assert result is False
    assert cfg.exists()
    assert accts.exists()
    assert merchants.exists()
    assert txn.exists()


def test_yes_is_case_and_whitespace_insensitive(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    cfg.write_text("{}")
    accts.write_text("{}")
    merchants.write_text("{}")
    txn.write_text("{}")

    result = run_reset(input_fn=lambda _: "  YES  ", output_fn=lambda *_: None)

    assert result is True
    assert not cfg.exists()
    assert not accts.exists()
    assert not merchants.exists()
    assert not txn.exists()


def test_nothing_to_reset(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    # None of the four files exist.
    lines = []

    def _prompt(_):
        raise AssertionError("should not prompt when nothing to reset")

    result = run_reset(input_fn=_prompt, output_fn=lines.append)

    assert result is False
    assert any("Nothing to reset" in line for line in lines)


def test_partial_existence_deletes_present_file(monkeypatch, tmp_path):
    cfg, accts, merchants, txn = _point_state_at(monkeypatch, tmp_path)
    txn.write_text("{}")  # only transactions.json exists

    result = run_reset(input_fn=lambda _: "y", output_fn=lambda *_: None)

    assert result is True
    assert not txn.exists()
    assert not cfg.exists()       # was never there
    assert not accts.exists()     # was never there
    assert not merchants.exists() # was never there
