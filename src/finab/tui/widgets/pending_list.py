"""PendingList — scrollable list of candidates with status glyphs.

This is a thin wrapper over Textual's ListView. Each ListItem renders
"GLYPH  ALIAS  AMOUNT" with glyph chosen by the candidate's status and
auto_reason. The cursor (which row is highlighted) is owned by ListView.
"""
from typing import Callable, Iterable, Optional

from textual.widgets import Label, ListItem, ListView

from finab.engine.sync import Candidate


# Status glyph mapping. Matches the spec.
_GLYPHS = {
    ("pending", None): "○",
    ("decided", None): "✓",
    ("auto", "inflow"): "+",
    ("auto", "transfer"): "→",
    ("auto", "no-merchant"): "✗",
    ("auto", "pre-month"): "↷",
    ("flushed", None): "⇡",
}


def _glyph_for(candidate: Candidate) -> str:
    """Pick the row glyph from candidate.status + candidate.auto_reason."""
    key_specific = (candidate.status, candidate.auto_reason)
    if key_specific in _GLYPHS:
        return _GLYPHS[key_specific]
    return _GLYPHS.get((candidate.status, None), "?")


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
        text = f"{glyph}  {alias:<18.18}  {amount:>10}"
        return ListItem(Label(text), id=f"row-{candidate.id}")

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
        """Rebuild the row for a candidate whose state changed (e.g.,
        after engine.apply_category). Uses clear-and-rebuild approach for
        simplicity and Textual version safety."""
        self.clear()
        for c in self._candidates:
            self.append(self._row(c))

    # ---- test helpers ----
    def row_glyphs_and_text(self) -> list[tuple[str, str]]:
        """Return [(glyph, full_text), ...] for testing."""
        result = []
        for item in self.children:
            label = item.query_one(Label)
            # Textual 8.x: Label.content (Static.content equivalent). Fall back to
            # renderable if content doesn't yield a usable string.
            text = str(getattr(label, "content", None) or getattr(label, "renderable", ""))
            glyph = text.split()[0] if text else ""
            result.append((glyph, text))
        return result
