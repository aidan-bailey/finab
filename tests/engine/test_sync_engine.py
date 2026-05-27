"""Tests for finab.engine.sync.SyncEngine and Candidate.

These exercise the headless state machine — no Textual, no client calls.
SyncEngine.flush is tested separately with a stub client.
"""
from dataclasses import is_dataclass

import pytest

from finab.engine.sync import Candidate


class TestCandidate:
    def test_is_a_dataclass(self):
        assert is_dataclass(Candidate)

    def test_default_status_is_pending(self):
        c = Candidate(id="abc", txn=object())
        assert c.status == "pending"
        assert c.auto_reason is None
        assert c.prior_state is None

    def test_can_set_status_and_auto_reason(self):
        c = Candidate(id="abc", txn=object(), status="auto", auto_reason="inflow")
        assert c.status == "auto"
        assert c.auto_reason == "inflow"
