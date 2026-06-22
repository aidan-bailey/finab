"""Tests for match_transfer_pairs — the pure transfer-pairing pass."""
from datetime import date

from finab.engine.sync import match_transfer_pairs, TransferMatch
from finab.models import Transaction
from finab.store import ConfigStore


def _store(tmp_path):
    s = ConfigStore(tmp_path / "config.json")
    s.add_account(
        alias="Cheque",
        fw_record={"id": "fw-a", "name": "Cheque", "type": "checking", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-a", "name": "Cheque", "type": "checking", "balance": 0, "transfer_payee_id": "tp-a"},
    )
    s.add_account(
        alias="Savings",
        fw_record={"id": "fw-b", "name": "Savings", "type": "savings", "balance": 0, "currency_code": "ZAR"},
        ynab_record={"id": "yn-b", "name": "Savings", "type": "savings", "balance": 0, "transfer_payee_id": "tp-b"},
    )
    return s


def _txn(uuid, amount, ynab_acc, *, day=10, merchant=None):
    return Transaction(
        import_id=uuid, fw_uuid=uuid, amount=amount,
        date=date(2026, 5, day), account_id=ynab_acc, merchant_id=merchant, memo="m",
    )


def test_same_day_exact_pair_is_high_confidence(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    matches = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)
    assert len(matches) == 1
    m = matches[0]
    assert isinstance(m, TransferMatch)
    assert m.keep_txn is out and m.suppress_txn is inn
    assert m.dest_transfer_payee_id == "tp-b"   # destination is the inflow's account
    assert m.dest_alias == "Savings"
    assert m.confidence == "high"


def test_next_day_same_merchant_is_high(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="shared")
    inn = _txn("i", 50000, "yn-b", day=11, merchant="shared")
    m = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)[0]
    assert m.confidence == "high"


def test_next_day_different_merchant_is_low(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="x")
    inn = _txn("i", 50000, "yn-b", day=11, merchant="y")
    m = match_transfer_pairs([out, inn], _store(tmp_path), window_days=1)[0]
    assert m.confidence == "low"


def test_multiple_candidates_is_low_and_prefers_shared_merchant(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10, merchant="shared")
    inn1 = _txn("i1", 50000, "yn-b", day=10, merchant="other")
    inn2 = _txn("i2", 50000, "yn-b", day=10, merchant="shared")
    matches = match_transfer_pairs([out, inn1, inn2], _store(tmp_path), window_days=1)
    assert len(matches) == 1
    assert matches[0].suppress_txn is inn2     # shared merchant wins the tie
    assert matches[0].confidence == "low"      # >1 candidate → low


def test_same_account_is_not_matched(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-a", day=10)
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_outside_window_is_not_matched(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=14)
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_destination_without_transfer_payee_is_skipped(tmp_path):
    s = _store(tmp_path)
    # Blank out Savings' transfer payee.
    acc = s.account_by_ynab_id("yn-b")
    acc["ynab"]["transfer_payee_id"] = None
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    assert match_transfer_pairs([out, inn], s, window_days=1) == []


def test_already_in_ynab_is_excluded(tmp_path):
    out = _txn("o", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    inn.ynab_id = "already-there"     # an update, not a create
    assert match_transfer_pairs([out, inn], _store(tmp_path), window_days=1) == []


def test_one_to_one_consumption(tmp_path):
    out1 = _txn("o1", -50000, "yn-a", day=10)
    out2 = _txn("o2", -50000, "yn-a", day=10)
    inn = _txn("i", 50000, "yn-b", day=10)
    matches = match_transfer_pairs([out1, out2, inn], _store(tmp_path), window_days=1)
    assert len(matches) == 1   # only one outflow can claim the single inflow
