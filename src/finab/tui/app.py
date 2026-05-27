"""FinabApp — root Textual application.

Plan 2 boot: shows a 'Hello finab' static and exits on 'q'. Plan 2
later tasks layer on the sidebar, screens, and data loading.
"""
from textual.app import App, ComposeResult
from textual.widgets import Static


class FinabApp(App):
    """Root app. Owns the sidebar (left) and content area (right)."""

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Hello finab", id="hello")
