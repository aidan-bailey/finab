"""FinabHeader — the persistent app header.

Layout: wordmark (left) — session stats (center) — current date (right).
Renders as a single line, no borders, deep-panel background.
"""
from datetime import date
from typing import Optional

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class FinabHeader(Widget):
    """Top-of-app header. Reads pending/decided counts from the
    SyncEngine via the parent app when refresh_stats() is called.

    Extends Widget (not Horizontal) so DEFAULT_CSS height: 1 is not
    overridden by Horizontal's inherited height: 1fr layout constraint.
    """

    DEFAULT_CSS = """
    FinabHeader {
        layout: horizontal;
        height: 1;
        background: $panel;
        padding: 0 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("FINAB", id="header-wordmark")
        yield Static("", id="header-stats")
        yield Static(date.today().isoformat(), id="header-date")

    def refresh_stats(self, pending: int = 0, decided: int = 0, flushed: int = 0) -> None:
        """Update the center stats panel."""
        try:
            stats = self.query_one("#header-stats", Static)
        except Exception:
            return
        parts = []
        if pending:
            parts.append(f"{pending} PENDING")
        if decided:
            parts.append(f"{decided} DECIDED")
        if flushed:
            parts.append(f"{flushed} FLUSHED")
        stats.update("   ·   ".join(parts) if parts else "")
