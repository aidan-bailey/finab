"""MerchantCard — detail view of a single MerchantsScreen row.

Shows different content based on whether the row is mapped or unmapped:
  - Unmapped: FW id, name (or "(none)"), and up to 3 sample transactions
    (date, amount, description) to help the user identify the merchant
    by sight.
  - Mapped: alias, YNAB linkage info, processings count.
  - Empty: placeholder text.

Updates via `set_row(kind, payload)` from MerchantsScreen on cursor moves.
"""
from typing import Optional

from textual.widgets import Static


def _amount_str(amount_milliunits: int) -> str:
    return f"{amount_milliunits / 1000:.2f}"


class MerchantCard(Static):
    """Read-only detail view of a MerchantsScreen row."""

    def set_row(self, kind: Optional[str], payload) -> None:
        """Render the detail for the given row.

        kind: "unmapped" | "mapped" | None
        payload: the FW merchant dict (unmapped) or store merchant dict (mapped)
        """
        if kind is None or payload is None:
            self.update("(no merchant selected)")
            return
        if kind == "unmapped":
            self._render_unmapped(payload)
        elif kind == "mapped":
            self._render_mapped(payload)
        else:
            self.update("(unknown row kind)")

    def _render_unmapped(self, fw_m: dict) -> None:
        """Render a FW merchant from _extract_distinct_merchants."""
        lines = [
            "UNMAPPED — press `l` to map",
            "",
            f"Name:   {fw_m.get('name') or '(none from FinWise)'}",
            f"FW ID:  {fw_m.get('id', '?')}",
            "",
        ]
        samples = fw_m.get("samples") or []
        if samples:
            lines.append("Recent transactions:")
            lines.append(f"  {'Date':<12} {'Amount':>10}   Description")
            for s in samples:
                date_str = s.get("date") or "?"
                amt = s.get("amount")
                if amt is not None:
                    # _extract_distinct_merchants stores amount as whole-currency
                    # float (it divides by 1000 itself). Format directly.
                    amt_str = f"{amt:>10.2f}"
                else:
                    amt_str = f"{'?':>10}"
                desc = s.get("description") or "(no description)"
                lines.append(f"  {date_str:<12} {amt_str}   {desc}")
                orig = s.get("original_description")
                if orig:
                    lines.append(f"  {'':<12} {'':>10}   └─ {orig}")
        else:
            lines.append("(no sample transactions captured)")
        self.update("\n".join(lines))

    def _render_mapped(self, m: dict) -> None:
        """Render a store merchant."""
        ynab = m.get("ynab") or {}
        if ynab.get("transfer_account_id"):
            link_kind = "transfer payee (own-account transfer)"
        elif ynab.get("id"):
            link_kind = "payee"
        else:
            link_kind = "(no YNAB linkage)"

        processings = m.get("processings") or {}
        cats = m.get("categories_used") or {}

        lines = [
            f"Alias:   {m.get('alias', '?')}",
            f"Linked:  {link_kind}",
            "",
            f"YNAB name: {ynab.get('name') or '(none)'}",
            f"YNAB id:   {ynab.get('id') or '(none)'}",
            "",
            f"Memory:    {len(processings)} amount(s), {len(cats)} category/categories",
        ]
        self.update("\n".join(lines))
