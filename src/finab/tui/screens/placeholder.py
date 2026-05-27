"""Placeholder screen used by Plan 2 for non-Sync sidebar entries.

Plan 3 replaces these with real implementations. For Plan 2, all five
sidebar entries point at this — only Sync gets a real screen.
"""
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Static


class PlaceholderScreen(Container):
    """A simple container that displays the screen name and a 'not yet'
    notice. Embedded inside the content switcher, not pushed as a Screen,
    so sidebar navigation feels instant.
    """

    def __init__(self, name: str, *, id: str = None):
        super().__init__(id=id)
        self._screen_name = name

    def compose(self) -> ComposeResult:
        yield Static(f"  {self._screen_name}\n\n  Not yet implemented (Plan 3).", classes="placeholder-body")
