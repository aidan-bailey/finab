"""FinabApp — root Textual application.

Layout: sidebar (left) + content switcher (right). On mount, kicks off
a background worker that fetches FW + YNAB data. The Sync screen waits
on that data; placeholder screens don't care.
"""
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView

from finab.tui.data_loader import LoadedData, load_all
from finab.tui.screens.placeholder import PlaceholderScreen
from finab.tui.screens.sync import SyncScreen




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
        ("q", "quit_with_confirm", "Quit"),
        ("c", "sync_category", "Category"),
        ("s", "sync_split", "Split"),
        ("r", "sync_history", "Repeat history"),
        ("t", "sync_force_transfer", "Force transfer"),
        ("u", "sync_undo", "Undo"),
        ("f", "sync_flush", "Flush"),
        Binding("enter", "sync_repeat_closest", "Repeat closest", priority=True),
        ("g", "sync_top", "Top"),
        ("G", "sync_bottom", "Bottom"),
        ("question_mark", "show_help", "Help"),
    ]

    def __init__(self, *, fw_client=None, ynab_client=None, budget_id: str = None, store=None, tx_store=None):
        """Construct. Clients and budget_id are injectable for tests; in
        production they default to real values built from .env."""
        super().__init__()
        self._fw_client = fw_client
        self._ynab_client = ynab_client
        self._budget_id = budget_id
        self._store = store
        self._tx_store = tx_store
        self.loaded: LoadedData | None = None

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield ListView(
                *[ListItem(Label(name), id=f"item-{sid}") for name, sid in SCREEN_IDS],
                id="sidebar",
            )
            with ContentSwitcher(initial="screen-sync", id="content-switcher"):
                yield SyncScreen(id="screen-sync")
                for name, sid in SCREEN_IDS[1:]:  # skip Sync (already yielded above)
                    yield PlaceholderScreen(name, id=sid)
        yield Footer()

    def on_mount(self) -> None:
        """After the layout is mounted, kick off the data fetch — but
        only if clients were provided. Tests that don't provide clients
        get a TUI shell with no data, which is fine."""
        if self._fw_client and self._ynab_client and self._budget_id:
            self._kickoff_load()

    @work(exclusive=True)
    async def _kickoff_load(self) -> None:
        self.loaded = await load_all(
            fw_client=self._fw_client,
            ynab_client=self._ynab_client,
            budget_id=self._budget_id,
        )
        if self.loaded.error is None and self._store and self._tx_store:
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.bind_data(
                loaded=self.loaded,
                store=self._store,
                tx_store=self._tx_store,
            )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        item_id = event.item.id
        if item_id and item_id.startswith("item-"):
            screen_id = item_id.removeprefix("item-")
            switcher = self.query_one("#content-switcher", ContentSwitcher)
            switcher.current = screen_id

    def _sync_screen_active(self) -> bool:
        """True when the Sync screen is the currently visible content pane."""
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-sync"

    def action_sync_category(self) -> None:
        if self._sync_screen_active():
            self.query_one(SyncScreen).action_category()

    def action_sync_split(self) -> None:
        if self._sync_screen_active():
            self.query_one(SyncScreen).action_split()

    def action_sync_history(self) -> None:
        if self._sync_screen_active():
            self.query_one(SyncScreen).action_history()

    def action_sync_undo(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_undo()

    def action_sync_flush(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_flush()

    def action_sync_repeat_closest(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_repeat_closest()

    def action_sync_force_transfer(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_force_transfer()

    def action_sync_top(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_top()

    def action_sync_bottom(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_bottom()

    def action_quit_with_confirm(self) -> None:
        """Quit, but if Sync has decided-but-not-flushed candidates,
        prompt the user first."""
        pending = self._pending_count()
        if pending == 0:
            self.exit()
            return
        from finab.tui.widgets.flush_confirm import FlushConfirmModal
        modal = FlushConfirmModal(pending_count=pending)
        self.push_screen(modal, callback=self._on_flush_confirm)

    def _pending_count(self) -> int:
        from finab.tui.screens.sync import SyncScreen
        try:
            sync_screen = self.query_one(SyncScreen)
        except Exception:
            return 0
        engine = getattr(sync_screen, "_engine", None)
        if engine is None:
            return 0
        return sum(
            1 for c in engine.candidates
            if c.status in ("decided", "auto")
        )

    def _on_flush_confirm(self, result) -> None:
        if result == "cancel":
            return
        if result == "flush":
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.action_flush()
        self.exit()

    def action_show_help(self) -> None:
        from finab.tui.widgets.help_overlay import HelpOverlay
        self.push_screen(HelpOverlay())
