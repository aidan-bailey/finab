"""FinabApp — root Textual application.

Layout: sidebar (left) + content switcher (right). Sidebar selection
changes the active content. Sync is the default.
"""
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Label, ListItem, ListView

from finab.tui.screens.placeholder import PlaceholderScreen


SCREEN_IDS = [
    ("Sync", "screen-sync"),
    ("Accounts", "screen-accounts"),
    ("Merchants", "screen-merchants"),
    ("Memory", "screen-memory"),
    ("Settings", "screen-settings"),
]


class FinabApp(App):
    """Root app: sidebar nav + content switcher."""

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(
                *[ListItem(Label(name), id=f"item-{sid}") for name, sid in SCREEN_IDS],
                id="sidebar",
            )
            with ContentSwitcher(initial="screen-sync", id="content-switcher"):
                for name, sid in SCREEN_IDS:
                    yield PlaceholderScreen(name, id=sid)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        """Highlight (cursor move) on the sidebar swaps the visible screen."""
        if event.item is None:
            return
        item_id = event.item.id  # e.g. "item-screen-accounts"
        if item_id and item_id.startswith("item-"):
            screen_id = item_id.removeprefix("item-")
            switcher = self.query_one("#content-switcher", ContentSwitcher)
            switcher.current = screen_id
