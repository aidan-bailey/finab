"""MemoryScreen — sidebar entry #4.

Flat list view of merchants and their processings entries. Headers
are merchant rows; child rows are individual entries.

Actions:
  d — delete the highlighted processing entry (no-op on a header)
  R — reset memory for the highlighted merchant (works on header or child)
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


class MemoryScreen(Container):
    """Sidebar entry #4."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None
        # Row index → (kind, merchant_id, amount_key_or_None).
        self._row_map: list = []

    def compose(self) -> ComposeResult:
        yield ListView(id="memory-list")

    def bind_data(self, *, store) -> None:
        self._store = store
        self.refresh_rows()

    def refresh_rows(self) -> None:
        lv = self.query_one("#memory-list", ListView)
        lv.clear()
        self._row_map = []
        if self._store is None:
            return
        for m in self._store.merchants():
            processings = m.get("processings") or {}
            n_proc = len(processings)
            n_cats = len(m.get("categories_used") or {})
            header = f"  {m['alias']}  ({n_proc} amts, {n_cats} cats)"
            # No explicit ID on ListItems (lesson from Task 7).
            lv.append(ListItem(Label(header)))
            self._row_map.append(("header", m["id"], None))
            for amt_key, entry in sorted(processings.items(), key=lambda kv: int(kv[0])):
                try:
                    amt = int(amt_key) / 1000.0
                    amt_str = f"{amt:>10.2f}"
                except (TypeError, ValueError):
                    amt_str = f"{amt_key:>10}"
                splits = entry.get("splits", []) or []
                if len(splits) == 1:
                    detail = splits[0].get("category_id", "?")
                else:
                    detail = f"split ({len(splits)} cats)"
                text = f"    {amt_str}   {detail}"
                lv.append(ListItem(Label(text)))
                self._row_map.append(("entry", m["id"], amt_key))

    def row_count(self) -> int:
        return len(self._row_map)

    def _current_row(self) -> Optional[tuple]:
        lv = self.query_one("#memory-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        return self._row_map[idx]

    # ---- public API ----

    def delete_entry(self, merchant_id: str, amount_key: str) -> None:
        if self._store is None:
            return
        self._store.delete_processing_entry(merchant_id, amount_key)
        self.refresh_rows()

    def reset_merchant(self, merchant_id: str) -> None:
        if self._store is None:
            return
        self._store.reset_merchant_memory(merchant_id)
        self.refresh_rows()

    # ---- actions ----

    def action_delete(self) -> None:
        row = self._current_row()
        if row is None or row[0] != "entry":
            self.app.bell()
            return
        _, merchant_id, amount_key = row
        self.delete_entry(merchant_id, amount_key)

    def action_reset(self) -> None:
        row = self._current_row()
        if row is None:
            return
        _, merchant_id, _ = row
        self.reset_merchant(merchant_id)
