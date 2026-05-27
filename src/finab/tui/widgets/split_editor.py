"""SplitEditorModal — live-editable split table.

State model: a list of rows; each row is {amount: int milliunits,
category_id: str | None, memo: str}. Initial state: one row holding
the full transaction amount, no category.

UX: a single Input at the bottom accepts commands. Commands:
  add <amount> <cat-id> [memo]   — append a row
  del N                          — delete row N (1-indexed)
  edit N <amount>                — update row N's amount
  cat N <cat-id>                 — set row N's category
Confirm (Ctrl+S) when balanced + every row has a category.
Cancel (Esc) any time.

This is intentionally minimal for Plan 2. A DataTable-based version
with inline cell editing is a Plan 3 polish task.

Public API for tests:
  current_rows() -> list[dict]
  set_rows(rows) — replace the whole row set (bypasses UI for tests)
  remaining_milliunits() -> int (zero when balanced)
"""
from typing import Mapping, Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


def _fmt(milli: int) -> str:
    return f"{milli / 1000:.2f}"


class SplitEditorModal(ModalScreen[Optional[list]]):
    """Returns a list[dict] of splits on confirm, or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
        ("ctrl+s", "confirm", "Confirm"),
    ]

    def __init__(
        self,
        *,
        txn_amount: int,
        categories: list,
        used_categories: Mapping[str, int],
        merchant_alias: str,
    ):
        super().__init__()
        self._txn_amount = txn_amount
        self._categories = categories
        self._used = dict(used_categories)
        self._merchant_alias = merchant_alias
        self._rows: list[dict] = [
            {"amount": txn_amount, "category_id": None, "memo": ""}
        ]

    def compose(self) -> ComposeResult:
        with Vertical(id="split-dialog"):
            yield Static(
                f"Split {self._merchant_alias} — total: {_fmt(self._txn_amount)}",
                id="split-title",
            )
            yield Static("", id="split-rows")
            yield Static("", id="split-remaining")
            yield Static(
                "  Commands: add <amount> <cat-id> [memo] | del N | edit N <amount> | cat N <cat-id> | Ctrl+S confirm | Esc cancel",
                id="split-help",
            )
            yield Input(placeholder="command…", id="split-input")

    def on_mount(self) -> None:
        self._render_state()
        self.query_one("#split-input", Input).focus()

    # ---- public API ----

    def current_rows(self) -> list[dict]:
        return list(self._rows)

    def set_rows(self, rows: list[dict]) -> None:
        self._rows = [dict(r) for r in rows]
        if self.is_mounted:
            self._render_state()

    def remaining_milliunits(self) -> int:
        return self._txn_amount - sum(r["amount"] for r in self._rows)

    # ---- rendering ----

    def _render_state(self) -> None:
        lines = []
        for i, row in enumerate(self._rows, start=1):
            cat = row["category_id"] or "(no cat)"
            memo = row["memo"] or ""
            lines.append(f"  {i}. {_fmt(row['amount']):>10}   cat={cat}   memo={memo}")
        self.query_one("#split-rows", Static).update("\n".join(lines) or "  (empty)")

        rem = self.remaining_milliunits()
        rem_text = f"  Remaining: {_fmt(rem)}"
        if rem == 0:
            rem_text += "  ✓ ready to confirm (Ctrl+S)"
        self.query_one("#split-remaining", Static).update(rem_text)

    # ---- actions ----

    def action_confirm(self) -> None:
        if self.remaining_milliunits() != 0:
            self.app.bell()
            return
        if any(r["category_id"] is None for r in self._rows):
            self.app.bell()
            return
        self.dismiss(self._rows)

    # ---- input handling ----

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "split-input":
            return
        raw = (event.value or "").strip()
        event.input.value = ""
        if not raw:
            return
        try:
            self._dispatch(raw)
        except ValueError:
            self.app.bell()
        self._render_state()

    def _dispatch(self, raw: str) -> None:
        parts = raw.split(maxsplit=3)
        cmd = parts[0].lower()
        if cmd == "add" and len(parts) >= 3:
            amt = int(round(float(parts[1]) * 1000))
            cat = parts[2]
            memo = parts[3] if len(parts) > 3 else ""
            self._rows.append({"amount": amt, "category_id": cat, "memo": memo})
            return
        if cmd == "del" and len(parts) == 2:
            n = int(parts[1])
            if 1 <= n <= len(self._rows):
                self._rows.pop(n - 1)
            return
        if cmd == "edit" and len(parts) >= 3:
            n = int(parts[1])
            if not (1 <= n <= len(self._rows)):
                raise ValueError("bad index")
            self._rows[n - 1]["amount"] = int(round(float(parts[2]) * 1000))
            return
        if cmd == "cat" and len(parts) >= 3:
            n = int(parts[1])
            if not (1 <= n <= len(self._rows)):
                raise ValueError("bad index")
            self._rows[n - 1]["category_id"] = parts[2]
            return
        raise ValueError(f"unknown command: {cmd!r}")
