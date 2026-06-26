from datetime import date

from finab.engine.sync import Candidate
from finab.models import Transaction
from finab.tui.widgets.transaction_card import TransactionCard


def _candidate(status, reason, dest):
    txn = Transaction(account_id="yn-a", date=date(2026, 5, 10), amount=-50000, memo="m")
    return Candidate(id="x", txn=txn, status=status, auto_reason=reason,
                     transfer_role="keep", transfer_dest_alias=dest)


def test_transfer_pair_status_label():
    card = TransactionCard()
    label = card._status_label(_candidate("auto", "transfer-pair", "Savings"))
    assert "TRANSFER-PAIR" in label
    assert "Savings" in label


def test_merged_status_label():
    card = TransactionCard()
    label = card._status_label(_candidate("merged", "transfer-merged", "Savings"))
    assert label.startswith("MERGED")
    assert "Savings" in label
