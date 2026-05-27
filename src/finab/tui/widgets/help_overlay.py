"""HelpOverlay — a static modal showing the app's keybindings.

Dismisses on Escape, Q, or `?`. No result.
"""
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


_HELP_TEXT = """\
finab — TUI keybindings

Navigation
  ↑/↓ or j/k    move cursor within a list
  g / G         jump to top / bottom (Sync screen)
  Tab           cycle focus between sidebar and main pane

Sync screen
  c             pick a category
  s             split into multiple categories
  r             repeat from history (pick prior categorization)
  Enter         repeat closest-amount history entry
  t             force-mark as a transfer to one of your accounts
  u             undo decision on the current row
  f             flush all decided/auto to YNAB

Modals
  Enter         confirm / select
  Esc           cancel / dismiss

App
  q             quit (confirms if pending decisions exist)
  ?             show this help
"""


class HelpOverlay(ModalScreen[None]):
    """Modal showing the keybindings cheat sheet."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Close"),
        ("question_mark", "dismiss(None)", "Close"),
        ("q", "dismiss(None)", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Static(_HELP_TEXT, id="help-text")
