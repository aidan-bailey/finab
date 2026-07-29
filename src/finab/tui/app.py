"""FinabApp — root Textual application.

Layout: sidebar (left) + content switcher (right). On mount, kicks off
a background worker that fetches FW + YNAB data. The Sync screen waits
on that data; placeholder screens don't care.
"""
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.theme import Theme
from textual.widgets import ContentSwitcher, Footer, Label, ListItem, ListView

from finab.tui.data_loader import LoadedData, load_all
from finab.tui.screens.accounts import AccountsScreen
from finab.tui.screens.memory import MemoryScreen
from finab.tui.screens.merchants import MerchantsScreen
from finab.tui.screens.placeholder import PlaceholderScreen
from finab.tui.screens.settings import SettingsScreen
from finab.tui.screens.sync import SyncScreen
from finab.tui.widgets.error_banner import ErrorBanner
from finab.tui.widgets.finab_header import FinabHeader
from finab.tui.widgets.wizard_banner import WizardBanner


FINAB_THEME = Theme(
    name="finab-editorial",
    primary="#c9591c",      # terracotta
    secondary="#1a5e63",    # deep teal
    accent="#c9591c",       # use primary as the visible accent
    foreground="#e8e3d5",   # cream
    background="#1a1612",   # warm charcoal
    success="#7a8a3a",      # muted olive
    warning="#c9a14b",      # amber
    error="#9a3a3a",        # rust red
    surface="#2a221d",
    panel="#221c17",
    boost="#3a2e26",
    dark=True,
    variables={
        "text-muted": "#8a7e6e",
    },
)




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

    # Tracks which sidebar screen is currently visible. Drives check_action
    # so the Footer only shows bindings relevant to the active screen.
    # bindings=True makes Textual auto-refresh the Footer when this changes.
    _active_screen = reactive("screen-sync", bindings=True)

    BINDINGS = [
        ("q", "quit_with_confirm", "Quit"),
        ("c", "sync_category", "Category"),
        ("s", "sync_split", "Split"),
        ("r", "sync_history", "Repeat history"),
        ("t", "sync_force_transfer", "Force transfer"),
        ("m", "sync_map_merchant", "Map merchant"),
        ("u", "sync_undo", "Undo"),
        ("f", "sync_flush", "Flush"),
        ("enter", "sync_repeat_closest", "Repeat closest"),
        ("n", "wizard_next", "Next step"),
        ("g", "sync_top", "Top"),
        ("G", "sync_bottom", "Bottom"),
        ("question_mark", "show_help", "Help"),
        ("a", "accounts_rename", "Rename"),
        ("l", "accounts_relink", "Relink"),
        ("i", "accounts_toggle_ignore", "Toggle ignore"),
        ("d", "memory_delete", "Delete entry"),
        ("R", "memory_reset", "Reset merchant"),
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
        # First-run setup wizard. None when inactive; otherwise one of
        # "budget" / "accounts" / "merchants" (the current step).
        self._wizard_step: str | None = None

    def compose(self) -> ComposeResult:
        yield FinabHeader(id="finab-header")
        yield ErrorBanner(id="error-banner")
        yield WizardBanner(id="wizard-banner")
        with Horizontal():
            yield ListView(
                *[ListItem(Label(name), id=f"item-{sid}") for name, sid in SCREEN_IDS],
                id="sidebar",
            )
            with ContentSwitcher(initial="screen-sync", id="content-switcher"):
                yield SyncScreen(id="screen-sync")
                yield AccountsScreen(id="screen-accounts")
                yield MerchantsScreen(id="screen-merchants")
                yield MemoryScreen(id="screen-memory")
                yield SettingsScreen(id="screen-settings")
        yield Footer()

    def on_mount(self) -> None:
        """After the layout is mounted, kick off the data fetch — but
        only if clients were provided. Tests that don't provide clients
        get a TUI shell with no data, which is fine."""
        # Register and activate the Editorial Terminal theme.
        self.register_theme(FINAB_THEME)
        self.theme = "finab-editorial"

        # Settings screen renders from local state — bind immediately.
        try:
            settings = self.query_one(SettingsScreen)
            settings.bind_data(budget_id=self._budget_id)
        except Exception:
            pass

        if self._fw_client and self._ynab_client:
            if self._budget_id:
                self._kickoff_load()
            else:
                # No budget configured (e.g. right after `finab --reset`).
                # Launch the first-run setup wizard.
                self._start_wizard()
        elif self._store is not None:
            try:
                self.query_one(AccountsScreen).bind_data(store=self._store)
                self.query_one(MerchantsScreen).bind_data(store=self._store)
                self.query_one(MemoryScreen).bind_data(store=self._store)
            except Exception:
                pass

    def _refresh_header_stats(self) -> None:
        """Read pending/decided/flushed counts from the SyncEngine (if loaded)
        and push them into the header."""
        try:
            header = self.query_one("#finab-header", FinabHeader)
        except Exception:
            return
        try:
            sync_screen = self.query_one(SyncScreen)
        except Exception:
            header.refresh_stats(0, 0, 0)
            return
        engine = getattr(sync_screen, "_engine", None)
        if engine is None:
            header.refresh_stats(0, 0, 0)
            return
        pending = sum(1 for c in engine.candidates if c.status == "pending")
        decided = sum(1 for c in engine.candidates if c.status in ("decided", "auto"))
        flushed = sum(1 for c in engine.candidates if c.status == "flushed")
        header.refresh_stats(pending, decided, flushed)

    def _render_error_banner(self) -> None:
        """Update the error banner from self.loaded.error (if any)."""
        try:
            banner = self.query_one("#error-banner", ErrorBanner)
        except Exception:
            return
        if self.loaded is not None and self.loaded.error is not None:
            banner.show(f"Fetch error: {self.loaded.error}")
        else:
            banner.hide()

    @work(exclusive=True)
    async def _kickoff_load(self) -> None:
        self.loaded = await load_all(
            fw_client=self._fw_client,
            ynab_client=self._ynab_client,
            budget_id=self._budget_id,
        )
        self._render_error_banner()
        if self.loaded.error is None and self._store and self._tx_store:
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.bind_data(
                loaded=self.loaded,
                store=self._store,
                tx_store=self._tx_store,
                ynab_payees=self.loaded.ynab_payees,
            )
            accounts_screen = self.query_one(AccountsScreen)
            accounts_screen.bind_data(
                store=self._store,
                fw_accounts=self.loaded.fw_accounts,
                ynab_accounts=self.loaded.ynab_accounts,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
            merchants_screen = self.query_one(MerchantsScreen)
            merchants_screen.bind_data(
                store=self._store,
                fw_transactions=self.loaded.fw_transactions,
                ynab_payees=self.loaded.ynab_payees,
                ynab_client=self._ynab_client,
                budget_id=self._budget_id,
            )
            memory_screen = self.query_one(MemoryScreen)
            memory_screen.bind_data(store=self._store)
        self._refresh_header_stats()

        # If the wizard kicked off this load (budget just chosen), advance
        # to the strict accounts step now that data is bound.
        if self._wizard_step == "budget" and self.loaded.error is None:
            self._enter_accounts_step()

    # --- First-run setup wizard ---

    _WIZARD_TOTAL = 3
    _WIZARD_STEP_SCREEN = {
        "accounts": "screen-accounts",
        "merchants": "screen-merchants",
    }

    @work(exclusive=True)
    async def _start_wizard(self) -> None:
        """Step 1: fetch YNAB budgets and present the picker. Entered when
        no budget_id is configured (first run / post-reset)."""
        try:
            budgets = self._ynab_client.get_budgets()
        except Exception as e:  # network/credential failure
            self.loaded = LoadedData(error=e)
            self._render_error_banner()
            return
        if not budgets:
            banner = self.query_one("#error-banner", ErrorBanner)
            banner.show("No YNAB budgets found for this token. Press q to quit.")
            return
        self._wizard_step = "budget"
        from finab.tui.widgets.budget_picker import BudgetPickerModal
        self.push_screen(
            BudgetPickerModal(budgets=budgets),
            callback=self._on_budget_chosen,
        )

    def _on_budget_chosen(self, budget_id) -> None:
        """Picker callback. None (cancel) → quit, since there is nothing to
        do without a budget. Otherwise persist it and kick off the load,
        which advances to the accounts step on completion."""
        if not budget_id:
            self.exit()
            return
        budget_id = str(budget_id)
        self._store.set_budget_id(budget_id)
        self._budget_id = budget_id
        try:
            self.query_one(SettingsScreen).bind_data(budget_id=budget_id)
        except Exception:
            pass
        self._kickoff_load()

    def _set_active_screen(self, screen_id: str) -> None:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        switcher.current = screen_id
        self._active_screen = screen_id

    def _enter_accounts_step(self) -> None:
        self._wizard_step = "accounts"
        self._set_active_screen("screen-accounts")
        self.query_one("#wizard-banner", WizardBanner).show(
            2, self._WIZARD_TOTAL, "map every account, then press n to continue"
        )

    def _enter_merchants_step(self) -> None:
        self._wizard_step = "merchants"
        self._set_active_screen("screen-merchants")
        self.query_one("#wizard-banner", WizardBanner).show(
            3, self._WIZARD_TOTAL, "map merchants (optional), then press n to finish"
        )

    def _finish_wizard(self) -> None:
        self._wizard_step = None
        self.query_one("#wizard-banner", WizardBanner).hide()
        self._set_active_screen("screen-sync")

    def action_wizard_next(self) -> None:
        if self._wizard_step == "accounts":
            try:
                remaining = self.query_one(AccountsScreen).unmapped_count()
            except Exception:
                remaining = 0
            if remaining > 0:
                self.query_one("#wizard-banner", WizardBanner).show(
                    2, self._WIZARD_TOTAL,
                    f"{remaining} account(s) still unmapped — map them, then press n",
                )
                self.bell()
                return
            self._enter_merchants_step()
        elif self._wizard_step == "merchants":
            self._finish_wizard()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None:
            return
        # Navigation lock: while the wizard is active, the sidebar can't be
        # used to skip ahead — snap the content back to the current step.
        if self._wizard_step is not None:
            locked = self._WIZARD_STEP_SCREEN.get(self._wizard_step, "screen-sync")
            self._set_active_screen(locked)
            return
        item_id = event.item.id
        if item_id and item_id.startswith("item-"):
            screen_id = item_id.removeprefix("item-")
            switcher = self.query_one("#content-switcher", ContentSwitcher)
            switcher.current = screen_id
            self._active_screen = screen_id

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

    def action_sync_map_merchant(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_map_merchant()

    def action_sync_top(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_top()

    def action_sync_bottom(self) -> None:
        if self._sync_screen_active():
            from finab.tui.screens.sync import SyncScreen
            self.query_one(SyncScreen).action_bottom()

    def _accounts_screen_active(self) -> bool:
        from textual.widgets import ContentSwitcher
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-accounts"

    def _merchants_screen_active(self) -> bool:
        from textual.widgets import ContentSwitcher
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-merchants"

    def action_accounts_rename(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_rename()
        elif self._merchants_screen_active():
            self.query_one(MerchantsScreen).action_rename()

    def action_accounts_relink(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_link()
        elif self._merchants_screen_active():
            self.query_one(MerchantsScreen).action_link()

    def action_accounts_toggle_ignore(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_toggle_ignore()

    def _memory_screen_active(self) -> bool:
        from textual.widgets import ContentSwitcher
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-memory"

    def action_memory_delete(self) -> None:
        if self._memory_screen_active():
            self.query_one(MemoryScreen).action_delete()

    def action_memory_reset(self) -> None:
        if self._memory_screen_active():
            self.query_one(MemoryScreen).action_reset()

    # --- Action visibility (Footer scoping) ---

    _ALWAYS_VISIBLE = {"quit_with_confirm", "show_help"}
    _SYNC_ACTIONS = {
        "sync_category", "sync_split", "sync_history",
        "sync_force_transfer", "sync_map_merchant", "sync_undo", "sync_flush",
        "sync_repeat_closest", "sync_top", "sync_bottom",
    }
    _ACCOUNTS_OR_MERCHANTS_ACTIONS = {"accounts_rename", "accounts_relink"}
    _ACCOUNTS_ONLY_ACTIONS = {"accounts_toggle_ignore"}
    _MEMORY_ACTIONS = {"memory_delete", "memory_reset"}

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Hide bindings that don't apply to the active screen.

        Returns True (visible + active), False (hidden), or None (grayed).
        We use True/False — never gray — since these bindings either apply
        or they don't.
        """
        if action in self._ALWAYS_VISIBLE:
            return True
        if action == "wizard_next":
            # Only live during the wizard; inert (and hidden) otherwise.
            return self._wizard_step is not None
        if action in self._SYNC_ACTIONS:
            return self._active_screen == "screen-sync"
        if action in self._ACCOUNTS_OR_MERCHANTS_ACTIONS:
            return self._active_screen in ("screen-accounts", "screen-merchants")
        if action in self._ACCOUNTS_ONLY_ACTIONS:
            return self._active_screen == "screen-accounts"
        if action in self._MEMORY_ACTIONS:
            return self._active_screen == "screen-memory"
        # Unknown action — allow (defensive).
        return True

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
