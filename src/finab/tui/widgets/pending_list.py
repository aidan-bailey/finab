"""PendingList — scrollable list of candidates with status glyphs.

This is a thin wrapper over Textual's ListView. Each ListItem renders
"GLYPH  ALIAS  AMOUNT" with glyph chosen by the candidate's status and
auto_reason. The cursor (which row is highlighted) is owned by ListView.

Each row splits glyph and text into two Labels so the glyph can carry
a per-status CSS class for color theming without affecting the rest of
the row text.
"""
from typing import Callable, Iterable, Optional

from textual.containers import Horizontal
from textual.widgets import Label, ListItem, ListView

from finab.engine.sync import Candidate


# Status glyph mapping. Matches the spec.
_GLYPHS = {
    ("pending", None): "○",
    ("pending", "no-merchant"): "✗",
    ("pending", "pre-month"): "↷",
    ("decided", None): "✓",
    ("auto", "inflow"): "+",
    ("auto", "transfer"): "→",
    # ("auto", "no-merchant") and ("auto", "pre-month") are no longer produced
    # by the engine, but leaving them in the map is harmless and helps if a
    # future change re-enables them.
    ("auto", "no-merchant"): "✗",
    ("auto", "pre-month"): "↷",
    ("auto", "transfer-pair"): "⇄",
    ("pending", "transfer-suggested"): "⇄",
    ("merged", "transfer-merged"): "⊝",
    ("merged", None): "⊝",
    ("flushed", None): "⇡",
}

_GLYPH_CSS_CLASS = {
    ("pending", None): "glyph-pending",
    ("pending", "no-merchant"): "glyph-no-merchant",
    ("pending", "pre-month"): "glyph-pre-month",
    ("decided", None): "glyph-decided",
    ("auto", "inflow"): "glyph-auto-inflow",
    ("auto", "transfer"): "glyph-auto-transfer",
    ("auto", "no-merchant"): "glyph-no-merchant",
    ("auto", "pre-month"): "glyph-pre-month",
    ("auto", "transfer-pair"): "glyph-auto-transfer",
    ("pending", "transfer-suggested"): "glyph-transfer-suggested",
    ("merged", "transfer-merged"): "glyph-merged",
    ("merged", None): "glyph-merged",
    ("flushed", None): "glyph-flushed",
}


def _glyph_for(candidate: Candidate) -> str:
    """Pick the row glyph from candidate.status + candidate.auto_reason.
    Warnings override status — a candidate with any warning shows ⚠.
    """
    if candidate.warnings:
        return "⚠"
    key_specific = (candidate.status, candidate.auto_reason)
    if key_specific in _GLYPHS:
        return _GLYPHS[key_specific]
    return _GLYPHS.get((candidate.status, None), "?")


def _glyph_class_for(candidate: Candidate) -> str:
    """Pick the CSS class for the glyph label based on status."""
    if candidate.warnings:
        return "glyph-warning"
    key = (candidate.status, candidate.auto_reason)
    if key in _GLYPH_CSS_CLASS:
        return _GLYPH_CSS_CLASS[key]
    return _GLYPH_CSS_CLASS.get((candidate.status, None), "glyph-pending")


def _amount_str(amount_milliunits: int) -> str:
    return f"{amount_milliunits / 1000:.2f}"


class PendingList(ListView):
    """ListView showing one ListItem per candidate. Candidates are passed
    in via constructor; the widget never re-fetches.

    alias_of(candidate) -> str: how to display the candidate's merchant
    alias. The TUI passes a closure that looks up the alias from the
    ConfigStore; tests pass a function that reads from a synthetic field.
    """

    def __init__(
        self,
        *,
        candidates: Iterable[Candidate],
        alias_of: Callable[[Candidate], str],
        id: Optional[str] = None,
    ):
        self._candidates = list(candidates)
        self._alias_of = alias_of
        items = [self._row(c) for c in self._candidates]
        super().__init__(*items, id=id)

    def _row(self, candidate: Candidate) -> ListItem:
        glyph = _glyph_for(candidate)
        alias = self._alias_of(candidate) or "(no merchant)"
        amount = _amount_str(candidate.txn.amount)
        rest = f"  {alias:<18.18}  {amount:>10}"
        glyph_class = _glyph_class_for(candidate)
        glyph_label = Label(glyph, classes=glyph_class)
        rest_label = Label(rest)
        return ListItem(Horizontal(glyph_label, rest_label), id=f"row-{candidate.id}")

    @property
    def candidates(self) -> list[Candidate]:
        return list(self._candidates)

    def current_candidate(self) -> Optional[Candidate]:
        """The candidate the cursor is on, or None if the list is empty."""
        idx = self.index
        if idx is None or idx < 0 or idx >= len(self._candidates):
            return None
        return self._candidates[idx]

    def refresh_row(self, candidate_id: str) -> None:
        """Update the display text of a row whose candidate state changed
        (e.g., after engine.apply_category or engine.undo).

        Rather than removing and re-mounting the ListItem (which races
        with Textual's deferred DOM mutations), we locate the existing
        row's Labels and update their content in-place. This avoids any
        duplicate-ID issue while keeping the row's position stable."""
        for i, c in enumerate(self._candidates):
            if c.id == candidate_id:
                items = list(self.children)
                if i < len(items):
                    labels = list(items[i].query(Label))
                    if len(labels) >= 2:
                        glyph = _glyph_for(c)
                        alias = self._alias_of(c) or "(no merchant)"
                        amount = _amount_str(c.txn.amount)
                        rest = f"  {alias:<18.18}  {amount:>10}"
                        labels[0].update(glyph)
                        # Clear and re-apply the glyph CSS class.
                        for cls in list(labels[0].classes):
                            labels[0].remove_class(cls)
                        labels[0].add_class(_glyph_class_for(c))
                        labels[1].update(rest)
                return

    # ---- test helpers ----
    def row_glyphs_and_text(self) -> list[tuple[str, str]]:
        """Return [(glyph, full_text), ...] for testing."""
        result = []
        for item in self.children:
            labels = list(item.query(Label))
            if len(labels) >= 2:
                glyph = str(getattr(labels[0], "content", "") or getattr(labels[0], "renderable", ""))
                text = str(getattr(labels[1], "content", "") or getattr(labels[1], "renderable", ""))
                result.append((glyph.strip(), glyph + text))
            elif labels:
                text = str(getattr(labels[0], "content", "") or getattr(labels[0], "renderable", ""))
                glyph_str = text.split()[0] if text else ""
                result.append((glyph_str, text))
        return result
