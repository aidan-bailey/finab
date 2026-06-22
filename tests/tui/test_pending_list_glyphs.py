from finab.engine.sync import Candidate
from finab.tui.widgets.pending_list import _glyph_for, _glyph_class_for


def _c(status, reason):
    return Candidate(id="x", txn=object(), status=status, auto_reason=reason)


def test_transfer_pair_glyph():
    c = _c("auto", "transfer-pair")
    assert _glyph_for(c) == "⇄"
    assert _glyph_class_for(c) == "glyph-auto-transfer"


def test_transfer_suggested_glyph():
    c = _c("pending", "transfer-suggested")
    assert _glyph_for(c) == "⇄"
    assert _glyph_class_for(c) == "glyph-transfer-suggested"


def test_transfer_merged_glyph():
    c = _c("merged", "transfer-merged")
    assert _glyph_for(c) == "⊝"
    assert _glyph_class_for(c) == "glyph-merged"
