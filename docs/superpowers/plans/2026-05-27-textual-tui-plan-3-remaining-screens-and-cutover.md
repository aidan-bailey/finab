# Textual TUI — Plan 3: Remaining Screens, Sync Polish, Cutover

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the Textual TUI migration. Polish the Sync screen (warnings, force-transfer, repeat-closest, nav, help, confirm-on-quit, error banner). Build the four remaining screens (Accounts, Merchants, Memory, Settings). Flip the default entrypoint from CLI to TUI. Delete the old prompt code.

**Architecture:** Add small mutation methods to `ConfigStore` (`set_account_alias`, `set_account_ignore`, `set_merchant_alias`, `delete_processing_entry`, `reset_merchant_memory`). Extend `SyncEngine` with `Candidate.warnings` and `apply_history()`. Each new screen is a `Container` mounted in `FinabApp`'s ContentSwitcher (mirroring Plan 2's SyncScreen pattern); each implements a list/detail layout with action keybindings. Cutover flips `main()` to launch the TUI by default; `--classic` runs the old CLI temporarily. Cleanup deletes the prompt code, the flag, and obsolete tests.

**Tech Stack:** Python 3.14, uv, Textual 8.2.7 (existing), no new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-27-textual-tui-design.md` (migration plan steps 5 finishing, plus 6 and 7).

**Scope boundary:** After Plan 3, `uv run finab` IS the TUI. No `FINAB_TUI` env flag, no `--classic` fallback (those are intermediate states). All five sidebar screens are live. The engine and store layer carry forward unchanged from Plan 2 except for the specific additions listed above.

---

## Lessons Learned from Plan 2 (apply to all tasks)

These came up repeatedly during Plan 2 execution and will recur here:

1. **Textual 8.x renames:** `Static.renderable` → `Static.content`. `ContentSwitcher` lives in `textual.widgets` (not `textual.containers`).
2. **ListView consumes single-character keys** for type-to-search. App-level BINDINGS with delegation to screen-level `action_*` methods is the working pattern (see `FinabApp.action_sync_category` etc. from Plan 2).
3. **ListView.clear()+append in tight succession races with Textual's deferred DOM removal** and raises `DuplicateIds`. Prefer in-place updates (`Label.update(new_text)`) over remove+remount where possible.
4. **OptionList.highlighted defaults to None** even after options are loaded. Set explicitly to enable Enter selection.
5. **Modal Enter activation needs the OptionList (not the Input)** to have focus. Either focus the OptionList in tests, or have the modal forward Input.Submitted to "pick the highlighted row" (Plan 2's CategoryPickerModal already does the latter).
6. **Local imports inside an `if` block** can shadow module-level imports for the whole function scope (Python parser marks the name local everywhere). Check existing top-of-file imports before adding inline imports.
7. **Class-body name shadowing:** `date = date(...)` inside a class body causes NameError. Alias the import.
8. **Amounts are milliunits** (1000 = $1.00). Every formatting helper does `/ 1000`.

---

## File Structure

**Created in this plan:**

```
src/finab/tui/
  widgets/
    error_banner.py       — ErrorBanner widget (red header for fetch failures)
    help_overlay.py       — HelpOverlay modal (? key)
    flush_confirm.py      — FlushConfirmModal (yes/no/cancel on quit)
    account_link_picker.py— AccountLinkPicker modal (fuzzy over store accounts)
    merchant_link_picker.py — MerchantLinkPicker modal (fuzzy over payees + own accounts)
  screens/
    accounts.py           — AccountsScreen
    merchants.py          — MerchantsScreen
    memory.py             — MemoryScreen
    settings.py           — SettingsScreen
```

**Modified in this plan:**

- `src/finab/store.py` — add 5 mutation methods (`set_account_alias`, `set_account_ignore`, `set_merchant_alias`, `delete_processing_entry`, `reset_merchant_memory`).
- `src/finab/engine/sync.py` — add `Candidate.warnings: list[str]`; add `SyncEngine.apply_history(candidate_id, entry)`; populate `warnings` for FW-transfer-but-unlinked candidates during `_build_candidate`.
- `src/finab/tui/widgets/pending_list.py` — render `⚠` glyph for candidates with warnings; render row tooltip text on detail card.
- `src/finab/tui/widgets/transaction_card.py` — display warnings inline.
- `src/finab/tui/screens/sync.py` — `action_force_transfer` (`t`), `action_repeat_closest` (Enter), `action_top` / `action_bottom` (`g` / `G`); fix HistoryPickerModal callback to call `engine.apply_history`.
- `src/finab/tui/app.py` — add `t` / `Enter` / `g` / `G` / `?` bindings + delegations; add `ErrorBanner` mounted above content; switch quit to `action_quit_with_confirm`; replace `PlaceholderScreen` for Accounts/Merchants/Memory/Settings with real screens.
- `src/finab/tui/styles.tcss` — styles for the new modals + error banner.
- `src/finab/main.py` — Cutover: TUI is default; `--classic` flag runs old CLI. Cleanup task removes `--classic` and the old code.
- `src/finab/transactions.py` — Cleanup: delete `_pick_category`, `_pick_category_from_full_list`, `_create_new_category`, `_prompt_memo`, `_collect_splits`, `_pick_from_processings`, `_confirm`, `_PendingQueue`, `_process_one_transaction`, `sync_transactions`. Keep `TransactionsStore`, `TRANSACTIONS_FILE`, and the re-exports from `engine/sync`.
- `tests/test_main.py` (if it exists) and `tests/test_sync_transactions.py` — Cleanup: delete tests that exercise the removed prompt code. Keep tests for moved/retained APIs.

**Untouched:** Everything in `src/finab/engine/accounts.py`, `engine/merchants.py`, `client.py`, `ynab_client.py`, `models.py`, `config.py` carries over from Plans 1 and 2.

---

## Task 1: Engine — Candidate.warnings + apply_history

Adds the missing engine surface that the TUI needs: a `warnings` field on `Candidate` (so the `⚠` glyph can render) and an `apply_history` method (so repeat-from-history decisions are undoable like other decisions).

**Files:**
- Modify: `src/finab/engine/sync.py`
- Modify: `tests/engine/test_sync_engine.py`

### Step 1: Write the failing tests

Append to `tests/engine/test_sync_engine.py`:

```python
class TestCandidateWarnings:
    def test_fw_transfer_with_unlinked_merchant_gets_warning(self, tmp_path):
        """When FinWise marks the txn as a transfer but the merchant isn't
        linked to an account's transfer payee, _build_candidate should
        populate `warnings` so the UI can render ⚠."""
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-wat", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-w1", amount=-5000,
            account_id="fw-acc-1", merchant_id="fw-merchant-wat",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
            is_transfer=True,  # FW says transfer
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        c = engine.candidates[0]
        assert len(c.warnings) >= 1
        assert any("transfer" in w.lower() for w in c.warnings)


class TestApplyHistory:
    def _setup(self, tmp_path):
        from datetime import date as date_cls
        store = _seeded_store(tmp_path)
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
        )
        tx_store = TransactionsStore(tmp_path / "transactions.json")
        today = date_cls.today()
        txn = _build_txn(
            fw_uuid="fw-h1", amount=-8421,
            account_id="fw-acc-1", merchant_id="fw-merchant-2",
            date_str=f"{today.year:04d}-{today.month:02d}-{today.day:02d}",
        )
        engine = SyncEngine(
            fw_transactions=[txn],
            ynab_transactions=[],
            ynab_categories=[],
            store=store,
            tx_store=tx_store,
        )
        return engine

    def test_apply_history_single_split(self, tmp_path):
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        entry = {
            "parent_memo": "weekly",
            "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groc"
        assert c.prior_state is not None  # snapshot taken — undo works

    def test_apply_history_multi_split_scales(self, tmp_path):
        """apply_history scales multi-split amounts to current txn.amount."""
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        # Entry was for -10000 originally; current txn is -8421.
        entry = {
            "parent_memo": "x",
            "splits": [
                {"category_id": "cat-a", "amount_milliunits": -6000, "memo": ""},
                {"category_id": "cat-b", "amount_milliunits": -4000, "memo": ""},
            ],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        assert c.txn.category_id is None
        assert len(c.txn.subtransactions) == 2
        # Scaled proportionally; sum must equal txn.amount exactly.
        total = sum(s["amount"] for s in c.txn.subtransactions)
        assert total == c.txn.amount

    def test_apply_history_supports_undo(self, tmp_path):
        engine = self._setup(tmp_path)
        c = engine.candidates[0]
        entry = {
            "parent_memo": "x",
            "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}],
        }
        engine.apply_history(c.id, entry=entry)
        assert c.status == "decided"
        engine.undo(c.id)
        assert c.status == "pending"
        assert c.txn.category_id is None
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/engine/test_sync_engine.py::TestCandidateWarnings tests/engine/test_sync_engine.py::TestApplyHistory -v`
Expected: FAIL — `AttributeError` on `c.warnings` and `AttributeError: 'SyncEngine' object has no attribute 'apply_history'`.

### Step 3: Add warnings field to Candidate

Edit `src/finab/engine/sync.py`. Find the `Candidate` dataclass and add `warnings`:

```python
@dataclass
class Candidate:
    """One transaction in the per-run workflow.

    `txn` is the FinWise-side Transaction; `merge_and_filter_transactions`
    may have already mutated its `import_id` to our durable id, and may
    have set `ynab_id` if this is an UPDATE rather than a CREATE.

    `warnings` holds human-readable strings the UI should surface non-
    destructively (e.g. via a ⚠ glyph) but that don't block flushing.
    """
    id: str
    txn: Any
    status: CandidateStatus = "pending"
    auto_reason: Optional[AutoReason] = None
    prior_state: Optional[dict] = None
    warnings: list = field(default_factory=list)
```

### Step 4: Populate warnings in _build_candidate

Find `_build_candidate` in the same file. The current logic checks `_is_transfer(merchant)` and auto-resolves. We need to ALSO check: if `txn.is_transfer` is True from FinWise but the merchant isn't a transfer payee, add a warning. Add this check **after** the transfer auto-rule fires (so transfers that DO link don't get a false warning) but **before** the no-merchant/pre-month rules. The cleanest place is right after the (b) Transfer block:

```python
        # (b) Transfer
        if _is_transfer(merchant):
            txn.payee_id = merchant["ynab"]["id"]
            txn.payee_name = None
            txn.category_id = None
            txn.subtransactions = []
            candidate.status = "auto"
            candidate.auto_reason = "transfer"
            return candidate

        # (b2) Warning: FW says transfer but merchant isn't a transfer payee.
        # We still flow through to the normal auto/pending paths — this
        # transaction will be pushed without a transfer payee linkage,
        # which is wrong but recoverable. Surface a warning so the user
        # can fix the merchant linkage later.
        if getattr(txn, "is_transfer", False):
            if merchant:
                candidate.warnings.append(
                    f"FinWise marks this as a transfer but merchant "
                    f"'{merchant.get('alias', '?')}' isn't linked to a YNAB "
                    f"account. Re-link via the Merchants screen."
                )
            else:
                candidate.warnings.append(
                    "FinWise marks this as a transfer but no merchant is "
                    "linked. It will push without a transfer payee."
                )
```

### Step 5: Add apply_history method

In the `SyncEngine` class, append after `apply_transfer` (and before `undo`):

```python
    def apply_history(
        self,
        candidate_id: str,
        *,
        entry: dict,
    ) -> None:
        """Record a repeat-from-history decision for the named candidate.

        `entry` is a `processings` entry: {parent_memo, splits} where
        splits is a list of {category_id, amount_milliunits, memo}.

        Multi-split entries are scaled proportionally to the current
        txn.amount (mirroring _apply_processing_to_txn from the engine
        helpers). This snapshots prior state so undo works the same
        as for apply_category / apply_split.

        Does update merchant memory (re-applying an entry counts as a
        categorization for that amount).
        """
        c = self._candidate(candidate_id)
        c.prior_state = self._snapshot(c.txn)
        # Delegate the mutation to the existing pure helper.
        _apply_processing_to_txn(entry, c.txn)
        merchant = self._store.merchant_by_finwise_id(
            getattr(c.txn, "merchant_id", None)
        )
        if merchant:
            _update_merchant_memory(self._store, merchant, c.txn)
        c.status = "decided"
```

### Step 6: Run the tests

Run: `uv run pytest tests/engine/test_sync_engine.py::TestCandidateWarnings tests/engine/test_sync_engine.py::TestApplyHistory -v`
Expected: PASS — all 4 tests.

### Step 7: Run the full suite

Run: `uv run pytest`
Expected: 203 passing (199 prior + 4 new). No regressions.

### Step 8: Commit

```bash
git add src/finab/engine/sync.py tests/engine/test_sync_engine.py
git commit -m "feat(engine): Candidate.warnings + SyncEngine.apply_history"
```

---

## Task 2: ConfigStore mutation methods

The new screens need to mutate the store in ways the existing API doesn't expose: rename aliases, toggle ignore, delete a single processing entry, reset a merchant's memory. Add these as small focused methods.

**Files:**
- Modify: `src/finab/store.py`
- Modify: `tests/test_store.py` (assumes it exists; if not, create it with these tests)

### Step 1: Write the failing tests

Append to `tests/test_store.py` (or create the file with the appropriate imports):

```python
class TestConfigStoreMutationsForTui:
    """Mutation methods added in Plan 3 for the TUI's Accounts/Merchants/Memory screens."""

    def _populated(self, tmp_path):
        store = ConfigStore(tmp_path / "config.json")
        store.add_account(
            alias="Chase Checking",
            fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
            ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
            ignore_transactions=False,
        )
        store.add_merchant(
            alias="Costco",
            fw_record={"id": "fw-merchant-1", "name": "Costco", "samples": []},
            ynab_record={"id": "yn-pay-1", "name": "Costco", "transfer_account_id": None},
        )
        # Seed processings on the merchant.
        store.set_merchant_memory(
            store.merchant_by_alias("Costco")["id"],
            categories_used={"cat-groc": 5, "cat-house": 2},
            processings={
                "-8421": {"parent_memo": "weekly", "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}]},
                "-1500": {"parent_memo": "snack", "splits": [{"category_id": "cat-house", "amount_milliunits": -1500, "memo": ""}]},
            },
        )
        return store

    def test_set_account_alias(self, tmp_path):
        store = self._populated(tmp_path)
        acc = store.account_by_finwise_id("fw-acc-1")
        store.set_account_alias(acc["id"], "Chase Primary")
        # Alias updated.
        assert store.account_by_finwise_id("fw-acc-1")["alias"] == "Chase Primary"
        # Index updated — old alias no longer resolves.
        assert store.account_by_alias("Chase Checking") is None
        # New alias resolves.
        assert store.account_by_alias("Chase Primary") is not None

    def test_set_account_ignore(self, tmp_path):
        store = self._populated(tmp_path)
        acc = store.account_by_finwise_id("fw-acc-1")
        assert acc["ignore_transactions"] is False
        store.set_account_ignore(acc["id"], True)
        assert store.account_by_finwise_id("fw-acc-1")["ignore_transactions"] is True
        # Idempotent.
        store.set_account_ignore(acc["id"], True)
        assert store.account_by_finwise_id("fw-acc-1")["ignore_transactions"] is True

    def test_set_merchant_alias(self, tmp_path):
        store = self._populated(tmp_path)
        m = store.merchant_by_finwise_id("fw-merchant-1")
        store.set_merchant_alias(m["id"], "Costco Wholesale")
        assert store.merchant_by_finwise_id("fw-merchant-1")["alias"] == "Costco Wholesale"
        assert store.merchant_by_alias("Costco") is None
        assert store.merchant_by_alias("Costco Wholesale") is not None

    def test_delete_processing_entry(self, tmp_path):
        store = self._populated(tmp_path)
        m = store.merchant_by_alias("Costco")
        assert "-8421" in m["processings"]
        store.delete_processing_entry(m["id"], "-8421")
        m_after = store.merchant_by_alias("Costco")
        assert "-8421" not in m_after["processings"]
        # Other entries untouched.
        assert "-1500" in m_after["processings"]
        # categories_used count not adjusted (memory ≠ stats — by design).
        assert m_after["categories_used"] == {"cat-groc": 5, "cat-house": 2}

    def test_delete_nonexistent_processing_is_noop(self, tmp_path):
        store = self._populated(tmp_path)
        m = store.merchant_by_alias("Costco")
        # Doesn't raise.
        store.delete_processing_entry(m["id"], "-99999")
        # Other entries untouched.
        assert store.merchant_by_alias("Costco")["processings"] == m["processings"]

    def test_reset_merchant_memory(self, tmp_path):
        store = self._populated(tmp_path)
        m = store.merchant_by_alias("Costco")
        store.reset_merchant_memory(m["id"])
        m_after = store.merchant_by_alias("Costco")
        assert m_after["categories_used"] == {}
        assert m_after["processings"] == {}
```

If `tests/test_store.py` doesn't exist, add an `import` block at the top:

```python
from finab.store import ConfigStore
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/test_store.py::TestConfigStoreMutationsForTui -v`
Expected: FAIL — methods don't exist.

### Step 3: Implement the mutation methods

Edit `src/finab/store.py`. Find the existing `set_merchant_memory` method (~line 185). Append after it:

```python
    def set_account_alias(self, account_id: str, alias: str) -> None:
        """Rename an account's alias. Rebuilds the alias index."""
        acc = self._data["accounts"][account_id]
        acc["alias"] = alias
        self._rebuild_indexes()
        self._save()

    def set_account_ignore(self, account_id: str, ignore: bool) -> None:
        """Toggle whether transactions on this account are processed."""
        acc = self._data["accounts"][account_id]
        acc["ignore_transactions"] = bool(ignore)
        self._save()

    def set_merchant_alias(self, merchant_id: str, alias: str) -> None:
        """Rename a merchant's alias. Rebuilds the alias index."""
        m = self._data["merchants"][merchant_id]
        m["alias"] = alias
        self._rebuild_indexes()
        self._save()

    def delete_processing_entry(self, merchant_id: str, amount_key: str) -> None:
        """Drop a single entry from a merchant's processings dict.

        Idempotent — silently no-ops if the key isn't present. Does NOT
        adjust `categories_used` counts; those are statistical and
        shouldn't change just because one historical entry was forgotten.
        """
        m = self._data["merchants"][merchant_id]
        processings = m.get("processings") or {}
        if amount_key in processings:
            del processings[amount_key]
            m["processings"] = processings
            self._save()

    def reset_merchant_memory(self, merchant_id: str) -> None:
        """Wipe both categories_used and processings on a merchant. The
        merchant entry itself (alias, FW/YNAB linkage) is preserved.
        """
        m = self._data["merchants"][merchant_id]
        m["categories_used"] = {}
        m["processings"] = {}
        self._save()
```

### Step 4: Run the tests

Run: `uv run pytest tests/test_store.py::TestConfigStoreMutationsForTui -v`
Expected: PASS — all 6 tests.

### Step 5: Run the full suite

Run: `uv run pytest`
Expected: 209 passing (203 prior + 6 new).

### Step 6: Commit

```bash
git add src/finab/store.py tests/test_store.py
git commit -m "feat(store): mutation methods for TUI screens (aliases, ignore, memory)"
```

---

## Task 3: Sync polish — ⚠ glyph + force_transfer + repeat_closest + g/G nav

Wires the engine warnings from Task 1 into the UI; adds three more SyncScreen actions (`t` to force-transfer, `Enter` to repeat closest, `g`/`G` for top/bottom).

**Files:**
- Modify: `src/finab/tui/widgets/pending_list.py`
- Modify: `src/finab/tui/widgets/transaction_card.py`
- Modify: `src/finab/tui/screens/sync.py`
- Modify: `src/finab/tui/app.py`
- Modify: `tests/tui/test_pending_list.py`
- Modify: `tests/tui/test_sync_screen.py`

### Step 1: Write the failing tests

Append to `tests/tui/test_pending_list.py`:

```python
@pytest.mark.asyncio
async def test_pending_list_warning_glyph():
    """A candidate with warnings gets the ⚠ glyph regardless of status."""
    from textual.app import App
    from finab.tui.widgets.pending_list import PendingList

    candidates = [_make_candidate(alias="Costco", amount=-50000, status="pending")]
    candidates[0].warnings = ["fake warning"]

    class _Host(App):
        def compose(self):
            yield PendingList(candidates=candidates, alias_of=lambda c: c.txn._alias, id="pl")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        pl = app.query_one("#pl", PendingList)
        rows = pl.row_glyphs_and_text()
        assert rows[0][0] == "⚠"  # warning glyph wins over status
```

Append to `tests/tui/test_sync_screen.py`:

```python
@pytest.mark.asyncio
async def test_pressing_enter_applies_closest_history(tmp_path):
    """Enter on a candidate with a closest-amount history entry should
    apply it via engine.apply_history."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-merchant-2", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-pay-2", "name": "Costco", "transfer_account_id": None},
    )
    store.set_merchant_memory(
        store.merchant_by_alias("Costco")["id"],
        categories_used={"cat-groc": 3},
        processings={"-8421": {"parent_memo": "x", "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}]}},
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    txn = Transaction(
        import_id="fw-e1",
        amount=-8421,
        date=today,
        memo="COSTCO",
        merchant_id="fw-merchant-2",
        account_id="fw-acc-1",
    )
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=LoadedData(fw_transactions=[txn]), store=store, tx_store=tx_store)
        await pilot.pause()
        pl = app.query_one("#sync-pending", PendingList)
        if pl.index is None:
            pl.index = 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        c = sync_screen._engine.candidates[0]
        assert c.status == "decided"
        assert str(c.txn.category_id) == "cat-groc"


@pytest.mark.asyncio
async def test_pressing_g_jumps_to_top(tmp_path):
    """g moves the cursor to row 0."""
    from datetime import date as date_cls
    from finab.models import Transaction
    from finab.store import ConfigStore
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData
    from finab.tui.screens.sync import SyncScreen
    from finab.tui.widgets.pending_list import PendingList

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-acc-1", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-acc-1", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpayee-1"},
        ignore_transactions=False,
    )
    tx_store = TransactionsStore(tmp_path / "transactions.json")
    today = date_cls.today()
    # Three no-merchant txns → three auto candidates.
    txns = [
        Transaction(import_id=f"fw-g{i}", amount=-1000-i, date=today, memo=f"M{i}", merchant_id=None, account_id="fw-acc-1")
        for i in range(3)
    ]
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sync_screen = app.query_one(SyncScreen)
        sync_screen.bind_data(loaded=LoadedData(fw_transactions=txns), store=store, tx_store=tx_store)
        await pilot.pause()
        pl = app.query_one("#sync-pending", PendingList)
        # Move cursor to bottom.
        pl.index = 2
        await pilot.pause()
        await pilot.press("g")
        await pilot.pause()
        assert pl.index == 0
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_pending_list.py::test_pending_list_warning_glyph tests/tui/test_sync_screen.py::test_pressing_enter_applies_closest_history tests/tui/test_sync_screen.py::test_pressing_g_jumps_to_top -v`
Expected: FAIL — warning glyph not rendered, Enter/g not bound.

### Step 3: Update PendingList to render the warning glyph

Edit `src/finab/tui/widgets/pending_list.py`. Find `_glyph_for(candidate)` and add a check at the top:

```python
def _glyph_for(candidate: Candidate) -> str:
    """Pick the row glyph from candidate.status + candidate.auto_reason.
    Warnings override status — a candidate with any warning shows ⚠.
    """
    if candidate.warnings:
        return "⚠"
    key_specific = (candidate.status, candidate.auto_reason)
    if key_specific in _GLYPHS:
        return _GLYPHS[key_specific]
    return _GLYPHS.get((candidate.status, None), "?")
```

### Step 4: Show warnings on the detail card

Edit `src/finab/tui/widgets/transaction_card.py`. Find `set_candidate`. After the existing `lines` list is built, before the `self.update(...)` call, append warning lines if any:

```python
        lines = [
            f"Merchant:  {alias}",
            f"Date:      {d}",
            f"Amount:    {amount}",
            f"Memo:      {memo}",
            f"Status:    {status_label}",
        ]
        if candidate.warnings:
            lines.append("")
            for w in candidate.warnings:
                lines.append(f"⚠ {w}")
        self.update("\n".join(lines))
```

### Step 5: Add Enter / t / g / G action methods on SyncScreen

Edit `src/finab/tui/screens/sync.py`. The existing class has `action_category`, `action_split`, `action_history`, `action_undo`, `action_flush`. Add four more:

```python
    def action_repeat_closest(self) -> None:
        """Apply the closest-amount processing entry for the merchant
        of the current candidate. No-op if no merchant or no processings."""
        c = self._current_candidate()
        if c is None or self._engine is None:
            return
        merchant_id = getattr(c.txn, "merchant_id", None)
        if not merchant_id:
            self.app.bell()
            return
        merchant = self._store.merchant_by_finwise_id(merchant_id)
        if not merchant:
            self.app.bell()
            return
        from finab.engine.sync import _closest_processing
        closest = _closest_processing(merchant, c.txn)
        if closest is None:
            self.app.bell()
            return
        _, entry = closest
        self._engine.apply_history(c.id, entry=entry)
        self._refresh_after_decision(c.id)

    def action_force_transfer(self) -> None:
        """Open a picker over the user's own accounts; selected account's
        transfer_payee_id is passed to engine.apply_transfer."""
        c = self._current_candidate()
        if c is None or self._engine is None or self._store is None:
            return
        from finab.tui.widgets.account_link_picker import AccountLinkPicker
        modal = AccountLinkPicker(store=self._store, title="Force transfer to which account?")

        def _on_picked(transfer_payee_id):
            if transfer_payee_id is None:
                return
            self._engine.apply_transfer(c.id, transfer_payee_id=transfer_payee_id)
            self._refresh_after_decision(c.id)

        self.app.push_screen(modal, callback=_on_picked)

    def action_top(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = 0
            from finab.tui.widgets.transaction_card import TransactionCard
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)

    def action_bottom(self) -> None:
        pl = self.query_one("#sync-pending", PendingList)
        if pl.candidates:
            pl.index = len(pl.candidates) - 1
            from finab.tui.widgets.transaction_card import TransactionCard
            card = self.query_one("#sync-detail", TransactionCard)
            card.set_candidate(pl.current_candidate(), alias_of=self._alias_of)
```

The `action_force_transfer` references `AccountLinkPicker` which we build in Task 6. For Task 3 testing the `t` binding doesn't need to be exercised yet — Task 6 will add the picker and the missing piece.

### Step 6: Add bindings + delegations to FinabApp

Edit `src/finab/tui/app.py`. Update `BINDINGS`:

```python
    BINDINGS = [
        ("q", "quit_with_confirm", "Quit"),
        ("c", "sync_category", "Category"),
        ("s", "sync_split", "Split"),
        ("r", "sync_history", "Repeat history"),
        ("t", "sync_force_transfer", "Force transfer"),
        ("u", "sync_undo", "Undo"),
        ("f", "sync_flush", "Flush"),
        ("enter", "sync_repeat_closest", "Repeat closest"),
        ("g", "sync_top", "Top"),
        ("G", "sync_bottom", "Bottom"),
        ("question_mark", "show_help", "Help"),
    ]
```

(Note: `q` binding now goes to `action_quit_with_confirm` — that action ships in Task 4. For Task 3 just declare the binding; the action method comes in Task 4. Similarly `show_help` ships in Task 5. The `t` binding works with `AccountLinkPicker` in Task 6.)

Add the new delegating actions:

```python
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
```

For Task 3, also add stubs for `action_quit_with_confirm` (so the binding doesn't error) and `action_show_help`:

```python
    def action_quit_with_confirm(self) -> None:
        # Filled in by Task 4. For now, behave like the old quit.
        self.exit()

    def action_show_help(self) -> None:
        # Filled in by Task 5. For now, no-op.
        pass
```

### Step 7: Wire the HistoryPickerModal callback to use engine.apply_history

In `src/finab/tui/screens/sync.py`, find `action_history`. The current callback `_on_picked` calls `_apply_processing_to_txn` directly and bypasses the engine. Replace it:

```python
        def _on_picked(result):
            if result is None:
                return
            _amount_key, entry = result
            self._engine.apply_history(c.id, entry=entry)
            self._refresh_after_decision(c.id)
```

Remove the old `from finab.engine.sync import _apply_processing_to_txn` import from that function — no longer needed.

### Step 8: Run all the new tests

Run: `uv run pytest tests/tui/test_pending_list.py tests/tui/test_sync_screen.py -v`
Expected: all pass (including the new warning-glyph and Enter tests).

### Step 9: Run the full suite

Run: `uv run pytest`
Expected: 212 passing (209 prior + 3 new).

### Step 10: Commit

```bash
git add src/finab/tui/widgets/pending_list.py src/finab/tui/widgets/transaction_card.py src/finab/tui/screens/sync.py src/finab/tui/app.py tests/tui/test_pending_list.py tests/tui/test_sync_screen.py
git commit -m "feat(tui): warnings glyph, repeat-closest, top/bottom nav; history via engine"
```

---

## Task 4: Quit-with-flush-confirm modal

Spec §Error handling §Ctrl+C: "if there are decided-but-not-flushed candidates, show a modal 'Flush N pending before exit? [Y/n/cancel]'". Plan 2 left this as a no-op exit. This task adds the modal and wires `q` (and Ctrl+C) to use it.

**Files:**
- Create: `src/finab/tui/widgets/flush_confirm.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_flush_confirm.py`

### Step 1: Write the failing test

Create `tests/tui/test_flush_confirm.py`:

```python
"""Tests for the FlushConfirmModal."""
import pytest


@pytest.mark.asyncio
async def test_flush_confirm_yes_flushes_then_exits():
    """Pressing y on the modal calls flush then exits."""
    from textual.app import App
    from finab.tui.widgets.flush_confirm import FlushConfirmModal

    actions = []

    class _Host(App):
        def on_mount(self):
            modal = FlushConfirmModal(pending_count=3)
            self.push_screen(modal, callback=lambda r: actions.append(r))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert actions == ["flush"]


@pytest.mark.asyncio
async def test_flush_confirm_no_exits_without_flushing():
    from textual.app import App
    from finab.tui.widgets.flush_confirm import FlushConfirmModal

    actions = []

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                FlushConfirmModal(pending_count=1),
                callback=lambda r: actions.append(r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
    assert actions == ["skip"]


@pytest.mark.asyncio
async def test_flush_confirm_cancel_stays_in_app():
    from textual.app import App
    from finab.tui.widgets.flush_confirm import FlushConfirmModal

    actions = []

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                FlushConfirmModal(pending_count=1),
                callback=lambda r: actions.append(r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert actions == ["cancel"]
```

### Step 2: Run the test to verify it fails

Run: `uv run pytest tests/tui/test_flush_confirm.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement FlushConfirmModal

Write `src/finab/tui/widgets/flush_confirm.py`:

```python
"""FlushConfirmModal — three-way prompt on quit with pending decisions.

Dismisses with one of:
  "flush"  — yes, push pending then exit
  "skip"   — no, exit without flushing
  "cancel" — never mind, stay in the app
"""
from typing import Literal

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


Result = Literal["flush", "skip", "cancel"]


class FlushConfirmModal(ModalScreen[Result]):
    """Three-way confirm before quitting with un-flushed decisions."""

    BINDINGS = [
        ("y", "dismiss('flush')", "Yes"),
        ("n", "dismiss('skip')", "No"),
        ("escape", "dismiss('cancel')", "Cancel"),
    ]

    def __init__(self, *, pending_count: int):
        super().__init__()
        self._pending_count = pending_count

    def compose(self) -> ComposeResult:
        with Vertical(id="flush-confirm-dialog"):
            yield Static(
                f"You have {self._pending_count} pending transaction(s) "
                f"that haven't been pushed to YNAB.",
                id="flush-confirm-message",
            )
            yield Static(
                "  y — Flush them and quit\n"
                "  n — Quit without flushing (they'll re-appear next sync)\n"
                "  Esc — Cancel, stay in the app",
                id="flush-confirm-options",
            )
```

### Step 4: Style the modal

Append to `src/finab/tui/styles.tcss`:

```tcss
FlushConfirmModal {
    align: center middle;
}

#flush-confirm-dialog {
    width: 60;
    height: auto;
    border: thick $warning;
    padding: 1 2;
    background: $surface;
}

#flush-confirm-message {
    padding-bottom: 1;
    text-style: bold;
}
```

### Step 5: Wire FlushConfirmModal into FinabApp

Edit `src/finab/tui/app.py`. Replace the stub `action_quit_with_confirm`:

```python
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

    def _on_flush_confirm(self, result: str) -> None:
        if result == "cancel":
            return
        if result == "flush":
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.action_flush()
        self.exit()
```

### Step 6: Run tests

Run: `uv run pytest tests/tui/test_flush_confirm.py -v`
Expected: PASS — all 3 tests.

### Step 7: Run the full suite

Run: `uv run pytest`
Expected: 215 passing.

### Step 8: Commit

```bash
git add src/finab/tui/widgets/flush_confirm.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_flush_confirm.py
git commit -m "feat(tui): FlushConfirmModal on quit with pending decisions"
```

---

## Task 5: Help overlay

A `?` keypress opens a `HelpOverlay` modal listing all the keybindings with descriptions. Static content — no state.

**Files:**
- Create: `src/finab/tui/widgets/help_overlay.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_help_overlay.py`

### Step 1: Write the failing test

Create `tests/tui/test_help_overlay.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_help_overlay_renders_keys():
    """HelpOverlay shows the key bindings for at least the Sync screen."""
    from textual.app import App
    from finab.tui.widgets.help_overlay import HelpOverlay

    class _Host(App):
        def on_mount(self):
            self.push_screen(HelpOverlay())

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Look for at least one key documented. We use the static content
        # rather than poking individual widgets.
        modal = app.screen
        all_text = ""
        from textual.widgets import Static
        for s in modal.query(Static):
            all_text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        assert "c" in all_text and "category" in all_text.lower()
        assert "Esc" in all_text or "esc" in all_text.lower()


@pytest.mark.asyncio
async def test_help_overlay_dismisses_on_escape():
    from textual.app import App
    from finab.tui.widgets.help_overlay import HelpOverlay

    class _Host(App):
        def on_mount(self):
            self.push_screen(HelpOverlay())

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Modal is up.
        assert isinstance(app.screen, HelpOverlay)
        await pilot.press("escape")
        await pilot.pause()
        # Modal dismissed.
        assert not isinstance(app.screen, HelpOverlay)
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_help_overlay.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement HelpOverlay

Write `src/finab/tui/widgets/help_overlay.py`:

```python
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
```

### Step 4: Style the help overlay

Append to `src/finab/tui/styles.tcss`:

```tcss
HelpOverlay {
    align: center middle;
}

#help-dialog {
    width: 70%;
    height: auto;
    max-height: 90%;
    border: thick $accent;
    padding: 1 2;
    background: $surface;
}
```

### Step 5: Wire ? key in FinabApp

Edit `src/finab/tui/app.py`. Replace the stub `action_show_help`:

```python
    def action_show_help(self) -> None:
        from finab.tui.widgets.help_overlay import HelpOverlay
        self.push_screen(HelpOverlay())
```

### Step 6: Run tests

Run: `uv run pytest tests/tui/test_help_overlay.py -v`
Expected: PASS — both tests.

### Step 7: Run the full suite

Run: `uv run pytest`
Expected: 217 passing.

### Step 8: Commit

```bash
git add src/finab/tui/widgets/help_overlay.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_help_overlay.py
git commit -m "feat(tui): HelpOverlay modal on ?"
```

---

## Task 6: ErrorBanner + AccountLinkPicker (shared modals)

`ErrorBanner` is a thin widget mounted at the top of the app showing fetch errors. `AccountLinkPicker` is a fuzzy-search modal over the store's accounts — used by Sync's `t` (force-transfer) and Accounts' `l` (link to YNAB). These two are bundled because they're both reusable infrastructure.

**Files:**
- Create: `src/finab/tui/widgets/error_banner.py`
- Create: `src/finab/tui/widgets/account_link_picker.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_error_banner.py`
- Create: `tests/tui/test_account_link_picker.py`

### Step 1: Write the failing tests

Create `tests/tui/test_error_banner.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_error_banner_hidden_by_default():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#error-banner")
        # Banner is mounted but has no text and 0 display height when empty.
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert text.strip() == ""


@pytest.mark.asyncio
async def test_error_banner_shows_loader_error():
    """If LoadedData.error is set, FinabApp surfaces it in the banner."""
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Inject an error result.
        app.loaded = LoadedData(error=RuntimeError("network down"))
        # The error banner should reflect the error after a refresh call.
        app._render_error_banner()
        await pilot.pause()
        banner = app.query_one("#error-banner")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert "network down" in text or "network down".lower() in text.lower()
```

Create `tests/tui/test_account_link_picker.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_account_link_picker_dismisses_with_transfer_payee_id(tmp_path):
    """Picking an account dismisses with its transfer_payee_id."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.store import ConfigStore
    from finab.tui.widgets.account_link_picker import AccountLinkPicker

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpa"},
        ignore_transactions=False,
    )

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                AccountLinkPicker(store=store, title="Pick an account"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder["value"] == "yn-tpa"
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_error_banner.py tests/tui/test_account_link_picker.py -v`
Expected: FAIL — modules don't exist.

### Step 3: Implement ErrorBanner

Write `src/finab/tui/widgets/error_banner.py`:

```python
"""ErrorBanner — mounted at the top of FinabApp; shows fetch errors.

When empty, hidden (no border, no padding). When `show(text)` is called,
displays a red bordered banner.
"""
from textual.widgets import Static


class ErrorBanner(Static):
    """Mounted at the top of the FinabApp. Empty by default."""

    DEFAULT_CSS = """
    ErrorBanner {
        background: $error 30%;
        color: $text;
        padding: 0 2;
        height: auto;
        display: none;
    }

    ErrorBanner.has-error {
        display: block;
    }
    """

    def __init__(self, *, id: str = None):
        super().__init__("", id=id)

    def show(self, message: str) -> None:
        self.update(message)
        self.add_class("has-error")

    def hide(self) -> None:
        self.update("")
        self.remove_class("has-error")
```

### Step 4: Implement AccountLinkPicker

Write `src/finab/tui/widgets/account_link_picker.py`:

```python
"""AccountLinkPicker — fuzzy-search modal over the store's accounts.

Dismisses with the chosen account's transfer_payee_id (a str) or None.

Used by:
  - Sync screen's `t` action (force-mark txn as transfer to this account)
  - Accounts screen actions (when relinking — though that one passes a
    different `on_select` semantics; we keep transfer_payee_id here for
    Sync's use case and parameterize later if needed)
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class AccountLinkPicker(ModalScreen[Optional[str]]):
    """Returns the chosen account's `transfer_payee_id` (str), or None."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, store, title: str = "Pick an account"):
        super().__init__()
        self._store = store
        self._title = title
        self._filter_text = ""
        self._all = [
            {
                "alias": a["alias"],
                "transfer_payee_id": (a.get("ynab") or {}).get("transfer_payee_id"),
            }
            for a in store.accounts()
            if (a.get("ynab") or {}).get("transfer_payee_id")
        ]

    def compose(self) -> ComposeResult:
        with Vertical(id="account-picker-dialog"):
            yield Static(self._title, id="account-picker-title")
            yield Input(placeholder="filter…", id="account-picker-filter")
            yield OptionList(id="account-picker-options")

    def on_mount(self) -> None:
        self._refresh()
        self.query_one("#account-picker-filter", Input).focus()

    def _refresh(self) -> None:
        ol = self.query_one("#account-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [a for a in self._all if not f or f in a["alias"].lower()]
        for a in rows:
            ol.add_option(Option(a["alias"], id=a["transfer_payee_id"]))
        if rows:
            ol.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "account-picker-filter":
            return
        self._filter_text = event.value or ""
        self._refresh()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ol = self.query_one("#account-picker-options", OptionList)
        idx = ol.highlighted
        if idx is None or ol.option_count == 0:
            return
        opt = ol.get_option_at_index(idx)
        self.dismiss(opt.id)
```

### Step 5: Mount ErrorBanner in FinabApp

Edit `src/finab/tui/app.py`. Import:

```python
from finab.tui.widgets.error_banner import ErrorBanner
```

In the `compose` method, yield the banner BEFORE the Horizontal container:

```python
def compose(self) -> ComposeResult:
    yield ErrorBanner(id="error-banner")
    with Horizontal():
        yield ListView(...)
        with ContentSwitcher(...):
            ...
    yield Footer()
```

Add a `_render_error_banner` method on FinabApp:

```python
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
```

Update `_kickoff_load` to call it after the load completes:

```python
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
            )
```

### Step 6: Style the account picker

Append to `src/finab/tui/styles.tcss`:

```tcss
AccountLinkPicker {
    align: center middle;
}

#account-picker-dialog {
    width: 60%;
    height: 60%;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#account-picker-title {
    text-style: bold;
    padding-bottom: 1;
}
```

### Step 7: Run the tests

Run: `uv run pytest tests/tui/test_error_banner.py tests/tui/test_account_link_picker.py -v`
Expected: PASS — all 3 tests.

### Step 8: Run the full suite

Run: `uv run pytest`
Expected: 220 passing.

### Step 9: Commit

```bash
git add src/finab/tui/widgets/error_banner.py src/finab/tui/widgets/account_link_picker.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_error_banner.py tests/tui/test_account_link_picker.py
git commit -m "feat(tui): ErrorBanner widget + AccountLinkPicker modal"
```

---

## Task 7: Accounts screen

The first of the four remaining sidebar screens. Lists every account in the store with state glyph + alias + linked YNAB name + type. Actions:
- `a` — rename alias (inline Input modal)
- `l` — relink to a different YNAB account (uses AccountLinkPicker, but returns the account_id NOT the transfer_payee_id — parameterize)
- `i` — toggle `ignore_transactions`
- `r` — refresh (no-op visual prompt for now; data is whatever the FinabApp loaded most recently)

**Files:**
- Create: `src/finab/tui/screens/accounts.py`
- Create: `src/finab/tui/widgets/alias_input.py` — small modal for "enter new alias"
- Modify: `src/finab/tui/widgets/account_link_picker.py` — add a `value_kind` parameter
- Modify: `src/finab/tui/app.py` — swap PlaceholderScreen("Accounts") for AccountsScreen + add `a`/`l`/`i` bindings
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_accounts_screen.py`

### Step 1: Write the failing tests

Create `tests/tui/test_accounts_screen.py`:

```python
import pytest


def _seed_store(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase Checking",
        fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpa"},
        ignore_transactions=False,
    )
    store.add_account(
        alias="Crypto Wallet",
        fw_record={"id": "fw-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "transfer_payee_id": "yn-tpb"},
        ignore_transactions=True,
    )
    return store


@pytest.mark.asyncio
async def test_accounts_screen_lists_accounts(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Switch to accounts screen.
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        await pilot.pause()
        # The accounts list should have 2 rows.
        assert ac_screen.row_count() == 2


@pytest.mark.asyncio
async def test_accounts_screen_toggle_ignore(tmp_path, monkeypatch):
    """Pressing `i` on the highlighted row toggles ignore_transactions in the store."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        ac_screen.set_cursor(0)
        await pilot.pause()
        # The first account ('Chase Checking') had ignore=False. Toggle.
        ac_screen.action_toggle_ignore()
        await pilot.pause()
        # Verify in the store.
        acc = store.account_by_finwise_id("fw-a")
        assert acc["ignore_transactions"] is True
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_accounts_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement AliasInputModal (small helper)

Write `src/finab/tui/widgets/alias_input.py`:

```python
"""AliasInputModal — single-line input prompt.

Dismisses with the entered string on Enter, or None on Escape.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


class AliasInputModal(ModalScreen[Optional[str]]):
    """Returns the entered alias string, or None on cancel."""

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, *, prompt: str, default: str = ""):
        super().__init__()
        self._prompt = prompt
        self._default = default

    def compose(self) -> ComposeResult:
        with Vertical(id="alias-input-dialog"):
            yield Static(self._prompt, id="alias-input-prompt")
            yield Input(value=self._default, id="alias-input-field")

    def on_mount(self) -> None:
        self.query_one("#alias-input-field", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        value = (event.value or "").strip()
        if not value:
            self.dismiss(None)
            return
        self.dismiss(value)
```

Style (append to `styles.tcss`):

```tcss
AliasInputModal {
    align: center middle;
}

#alias-input-dialog {
    width: 50;
    height: auto;
    border: thick $primary;
    padding: 1 2;
    background: $surface;
}

#alias-input-prompt {
    padding-bottom: 1;
}
```

### Step 4: Extend AccountLinkPicker with value_kind

Edit `src/finab/tui/widgets/account_link_picker.py`. Currently the modal always returns `transfer_payee_id`. Add a `value_kind` parameter:

```python
class AccountLinkPicker(ModalScreen[Optional[str]]):
    """Returns the chosen account's id according to value_kind:
       - "transfer_payee_id" (default): for force-marking transfers
       - "account_internal_id": for relinking from the Accounts screen
       - "ynab_account_id": for tasks that need the YNAB-side id directly
    """

    def __init__(
        self,
        *,
        store,
        title: str = "Pick an account",
        value_kind: str = "transfer_payee_id",
    ):
        super().__init__()
        self._store = store
        self._title = title
        self._value_kind = value_kind
        self._filter_text = ""
        self._all = self._collect()

    def _collect(self) -> list:
        rows = []
        for a in self._store.accounts():
            ynab = a.get("ynab") or {}
            if self._value_kind == "transfer_payee_id":
                value = ynab.get("transfer_payee_id")
                if not value:
                    continue
            elif self._value_kind == "account_internal_id":
                value = a.get("id")
            elif self._value_kind == "ynab_account_id":
                value = ynab.get("id")
                if not value:
                    continue
            else:
                continue
            rows.append({"alias": a["alias"], "value": value})
        return rows
```

Update the `_refresh` method to use `a["value"]`:

```python
    def _refresh(self) -> None:
        ol = self.query_one("#account-picker-options", OptionList)
        ol.clear_options()
        f = self._filter_text.lower()
        rows = [a for a in self._all if not f or f in a["alias"].lower()]
        for a in rows:
            ol.add_option(Option(a["alias"], id=a["value"]))
        if rows:
            ol.highlighted = 0
```

### Step 5: Implement AccountsScreen

Write `src/finab/tui/screens/accounts.py`:

```python
"""AccountsScreen — sidebar entry #2.

Lists all FW-mapped accounts with state glyph + alias + linked YNAB
record. Actions:
  a — rename alias (inline modal)
  l — relink to a different YNAB account (open AccountLinkPicker)
  i — toggle ignore_transactions
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


def _state_glyph(account: dict) -> str:
    """Pick the glyph based on linked/ignored state."""
    if account.get("ignore_transactions"):
        return "⏸"
    if (account.get("ynab") or {}).get("id"):
        return "✓"
    return "!"


class AccountsScreen(Container):
    """Sidebar entry #2 — browse and edit account mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None

    def compose(self) -> ComposeResult:
        yield ListView(id="accounts-list")

    def bind_data(self, *, store) -> None:
        self._store = store
        self.refresh_rows()

    def refresh_rows(self) -> None:
        """Rebuild the ListView from the current store state."""
        lv = self.query_one("#accounts-list", ListView)
        # Clear-and-repopulate is unsafe on rapid re-entry; instead,
        # mount-then-remove pattern. For Plan 3 we lazily prefer
        # clear()+wait then append, since this screen isn't refreshed
        # multiple times per second.
        # Actually: simplest is to update each existing Label in place
        # if the row count hasn't changed, and fall back to clear+append
        # otherwise. For Plan 3, accept clear+append — the race only
        # manifests for back-to-back calls.
        lv.clear()
        if self._store is None:
            return
        for acc in self._store.accounts():
            glyph = _state_glyph(acc)
            alias = acc.get("alias", "?")
            ynab = acc.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            yn_type = ynab.get("type") or ""
            tag = " (tracking)" if yn_type in {
                "otherAsset", "otherLiability", "mortgage", "autoLoan",
                "studentLoan", "personalLoan", "medicalDebt", "otherDebt",
            } else ""
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<22.22}  {yn_type}{tag}"
            lv.append(ListItem(Label(text), id=f"acc-row-{acc['id']}"))

    def row_count(self) -> int:
        return len(list(self._store.accounts())) if self._store else 0

    def set_cursor(self, index: int) -> None:
        self.query_one("#accounts-list", ListView).index = index

    def _current_account(self) -> Optional[dict]:
        lv = self.query_one("#accounts-list", ListView)
        idx = lv.index
        if idx is None or self._store is None:
            return None
        accounts = list(self._store.accounts())
        if 0 <= idx < len(accounts):
            return accounts[idx]
        return None

    def action_toggle_ignore(self) -> None:
        acc = self._current_account()
        if acc is None or self._store is None:
            return
        self._store.set_account_ignore(acc["id"], not acc.get("ignore_transactions"))
        self.refresh_rows()

    def action_rename(self) -> None:
        acc = self._current_account()
        if acc is None or self._store is None:
            return
        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Rename '{acc['alias']}':",
            default=acc.get("alias", ""),
        )

        def _on_done(new_alias):
            if new_alias is None or new_alias == acc.get("alias"):
                return
            self._store.set_account_alias(acc["id"], new_alias)
            self.refresh_rows()

        self.app.push_screen(modal, callback=_on_done)

    def action_relink(self) -> None:
        """Open AccountLinkPicker over YNAB accounts and relink the current
        account to the chosen one. For Plan 3 we use account_internal_id
        as the value kind, then look up the ynab record from that internal id."""
        acc = self._current_account()
        if acc is None or self._store is None:
            return
        # In Plan 3, "relink" picks from a separate set of YNAB accounts
        # the user wants to link to. AccountLinkPicker scans the store's
        # accounts; that's not what we want here. We'd need a different
        # picker over the YNAB-side accounts list fetched from the API.
        # For Plan 3, keep this action as a placeholder that bells —
        # Plan 4 (or a follow-up) adds a proper YNAB-account picker.
        # This isn't great UX but keeps Plan 3 scoped.
        self.app.bell()
```

### Step 6: Mount AccountsScreen in FinabApp

Edit `src/finab/tui/app.py`. Import:

```python
from finab.tui.screens.accounts import AccountsScreen
```

In the `compose` method, change the ContentSwitcher contents. Currently:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    for name, sid in SCREEN_IDS[1:]:  # skip Sync
        yield PlaceholderScreen(name, id=sid)
```

Change to:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    yield AccountsScreen(id="screen-accounts")
    for name, sid in SCREEN_IDS[2:]:  # skip Sync + Accounts
        yield PlaceholderScreen(name, id=sid)
```

Update `_kickoff_load` to call `bind_data` on the AccountsScreen too (after the SyncScreen bind):

```python
        if self.loaded.error is None and self._store and self._tx_store:
            from finab.tui.screens.sync import SyncScreen
            sync_screen = self.query_one(SyncScreen)
            sync_screen.bind_data(
                loaded=self.loaded,
                store=self._store,
                tx_store=self._tx_store,
            )
            accounts_screen = self.query_one(AccountsScreen)
            accounts_screen.bind_data(store=self._store)
```

Also handle the case where store is passed but no clients (so on_mount doesn't run the worker). Add to `on_mount`:

```python
    def on_mount(self) -> None:
        if self._fw_client and self._ynab_client and self._budget_id:
            self._kickoff_load()
        elif self._store is not None:
            # Tests construct FinabApp with just a store. Bind the screens
            # that don't need fetched data.
            try:
                accounts_screen = self.query_one(AccountsScreen)
                accounts_screen.bind_data(store=self._store)
            except Exception:
                pass
```

### Step 7: Add bindings for AccountsScreen actions

Edit `src/finab/tui/app.py` `BINDINGS`. Add (after the Sync entries):

```python
        ("a", "accounts_rename", "Rename"),
        ("l", "accounts_relink", "Relink"),
        ("i", "accounts_toggle_ignore", "Toggle ignore"),
```

Add a helper:

```python
    def _accounts_screen_active(self) -> bool:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-accounts"
```

Add the delegating actions:

```python
    def action_accounts_rename(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_rename()

    def action_accounts_relink(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_relink()

    def action_accounts_toggle_ignore(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_toggle_ignore()
```

Note: `a` is also a single character that ListView consumes when in type-search mode. Same as `c/s/r` in Plan 2 — app-level bindings win when the focused widget chain doesn't consume them. If a test fails because `a` triggers type-search on the inner ListView, focus the AccountsScreen container or its outer ListView's parent first; the existing `_accounts_screen_active` check should still gate properly.

### Step 8: Style the accounts list

Append to `src/finab/tui/styles.tcss`:

```tcss
AccountsScreen {
    width: 1fr;
    height: 1fr;
    padding: 1 2;
}

#accounts-list {
    height: 1fr;
}
```

### Step 9: Run tests

Run: `uv run pytest tests/tui/test_accounts_screen.py -v`
Expected: PASS — both tests.

### Step 10: Run full suite

Run: `uv run pytest`
Expected: 222 passing.

### Step 11: Commit

```bash
git add src/finab/tui/screens/accounts.py src/finab/tui/widgets/alias_input.py src/finab/tui/widgets/account_link_picker.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_accounts_screen.py
git commit -m "feat(tui): AccountsScreen with rename + toggle-ignore actions"
```

---

## Task 8: Merchants screen

Lists every FW-mapped merchant with state glyph (✓ linked-payee, → linked-transfer-payee, ! unlinked). Actions:
- `a` — rename alias (reuses `AliasInputModal`)
- `l` — relink (Plan 3 scope: bell, full implementation deferred — same Plan 4 note as Accounts.action_relink)
- `r` — refresh (no-op for now)

**Files:**
- Create: `src/finab/tui/screens/merchants.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_merchants_screen.py`

### Step 1: Write the failing tests

Create `tests/tui/test_merchants_screen.py`:

```python
import pytest


def _seed_store_with_merchants(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-m1", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-p1", "name": "Costco", "transfer_account_id": None},
    )
    store.add_merchant(
        alias="Self → Savings",
        fw_record={"id": "fw-m2", "name": "Self Transfer", "samples": []},
        ynab_record={"id": "yn-p2", "name": "Transfer: Savings", "transfer_account_id": "yn-sav"},
    )
    return store


@pytest.mark.asyncio
async def test_merchants_screen_lists_merchants(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        screen = app.query_one(MerchantsScreen)
        screen.refresh_rows()
        await pilot.pause()
        assert screen.row_count() == 2


@pytest.mark.asyncio
async def test_merchants_screen_rename(tmp_path):
    """action_rename opens AliasInputModal; on success, store is updated."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.merchants import MerchantsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from textual.widgets import ContentSwitcher

    store = _seed_store_with_merchants(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-merchants"
        await pilot.pause()
        screen = app.query_one(MerchantsScreen)
        screen.refresh_rows()
        screen.set_cursor(0)
        await pilot.pause()
        # Trigger rename action programmatically so we don't have to type.
        screen.action_rename()
        await pilot.pause()
        # Modal should be up.
        assert isinstance(app.screen, AliasInputModal)
        # Type a new alias.
        await pilot.press(*"Costco Wholesale")
        await pilot.press("enter")
        await pilot.pause()
        # Modal dismissed; store updated.
        assert store.merchant_by_alias("Costco Wholesale") is not None
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_merchants_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement MerchantsScreen

Write `src/finab/tui/screens/merchants.py`:

```python
"""MerchantsScreen — sidebar entry #3.

Lists merchants with state glyph + alias + linked-to.
"""
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, ListItem, ListView


def _merchant_glyph(m: dict) -> str:
    ynab = m.get("ynab") or {}
    if ynab.get("transfer_account_id"):
        return "→"
    if ynab.get("id"):
        return "✓"
    return "!"


class MerchantsScreen(Container):
    """Sidebar entry #3 — browse and edit merchant mappings."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._store = None

    def compose(self) -> ComposeResult:
        yield ListView(id="merchants-list")

    def bind_data(self, *, store) -> None:
        self._store = store
        self.refresh_rows()

    def refresh_rows(self) -> None:
        lv = self.query_one("#merchants-list", ListView)
        lv.clear()
        if self._store is None:
            return
        for m in self._store.merchants():
            glyph = _merchant_glyph(m)
            alias = m.get("alias", "?")
            ynab = m.get("ynab") or {}
            yn_name = ynab.get("name") or "(unlinked)"
            link_kind = "transfer payee" if ynab.get("transfer_account_id") else ("payee" if ynab.get("id") else "")
            text = f"{glyph}  {alias:<22.22}  →  {yn_name:<26.26}  {link_kind}"
            lv.append(ListItem(Label(text), id=f"m-row-{m['id']}"))

    def row_count(self) -> int:
        return len(list(self._store.merchants())) if self._store else 0

    def set_cursor(self, index: int) -> None:
        self.query_one("#merchants-list", ListView).index = index

    def _current_merchant(self) -> Optional[dict]:
        lv = self.query_one("#merchants-list", ListView)
        idx = lv.index
        if idx is None or self._store is None:
            return None
        merchants = list(self._store.merchants())
        if 0 <= idx < len(merchants):
            return merchants[idx]
        return None

    def action_rename(self) -> None:
        m = self._current_merchant()
        if m is None or self._store is None:
            return
        from finab.tui.widgets.alias_input import AliasInputModal
        modal = AliasInputModal(
            prompt=f"Rename '{m['alias']}':",
            default=m.get("alias", ""),
        )

        def _on_done(new_alias):
            if new_alias is None or new_alias == m.get("alias"):
                return
            self._store.set_merchant_alias(m["id"], new_alias)
            self.refresh_rows()

        self.app.push_screen(modal, callback=_on_done)

    def action_relink(self) -> None:
        # Plan 3 scope: bell. Plan 4 (or follow-up) implements proper
        # picker over YNAB payees + own accounts.
        self.app.bell()
```

### Step 4: Mount MerchantsScreen in FinabApp + bindings

Edit `src/finab/tui/app.py`. Import:

```python
from finab.tui.screens.merchants import MerchantsScreen
```

Change the compose loop:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    yield AccountsScreen(id="screen-accounts")
    yield MerchantsScreen(id="screen-merchants")
    for name, sid in SCREEN_IDS[3:]:  # skip Sync + Accounts + Merchants
        yield PlaceholderScreen(name, id=sid)
```

Add screen-active helper:

```python
    def _merchants_screen_active(self) -> bool:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-merchants"
```

Add to `_kickoff_load` (and the `on_mount` test-shortcut path) the merchants bind:

```python
            merchants_screen = self.query_one(MerchantsScreen)
            merchants_screen.bind_data(store=self._store)
```

Both `a` and `l` bindings need to dispatch to the right screen based on which is active. Update the existing `action_accounts_rename` and add merchants version:

```python
    def action_accounts_rename(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_rename()
        elif self._merchants_screen_active():
            self.query_one(MerchantsScreen).action_rename()

    def action_accounts_relink(self) -> None:
        if self._accounts_screen_active():
            self.query_one(AccountsScreen).action_relink()
        elif self._merchants_screen_active():
            self.query_one(MerchantsScreen).action_relink()
```

Rename the action methods to be more screen-neutral. Update BINDINGS:

```python
        ("a", "rename", "Rename"),
        ("l", "relink", "Relink"),
        ("i", "accounts_toggle_ignore", "Toggle ignore"),
```

Rename `action_accounts_rename` → `action_rename`; `action_accounts_relink` → `action_relink`. The toggle-ignore action stays accounts-specific since merchants don't have ignore_transactions.

### Step 5: Style

Append to `src/finab/tui/styles.tcss`:

```tcss
MerchantsScreen {
    width: 1fr;
    height: 1fr;
    padding: 1 2;
}

#merchants-list {
    height: 1fr;
}
```

### Step 6: Run tests

Run: `uv run pytest tests/tui/test_merchants_screen.py tests/tui/test_accounts_screen.py -v`
Expected: PASS — all tests including the existing accounts ones.

### Step 7: Run full suite

Run: `uv run pytest`
Expected: 224 passing.

### Step 8: Commit

```bash
git add src/finab/tui/screens/merchants.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_merchants_screen.py
git commit -m "feat(tui): MerchantsScreen with rename action"
```

---

## Task 9: Memory screen

Lists merchants and lets the user expand a merchant to see its `processings` entries. Actions:
- `d` — delete the currently-highlighted processing entry
- `R` (capital R) — reset entire merchant memory
- Enter / arrow keys — navigate

**Files:**
- Create: `src/finab/tui/screens/memory.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_memory_screen.py`

### Step 1: Write the failing tests

Create `tests/tui/test_memory_screen.py`:

```python
import pytest


def _seed_with_memory(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_merchant(
        alias="Costco",
        fw_record={"id": "fw-m1", "name": "Costco", "samples": []},
        ynab_record={"id": "yn-p1", "name": "Costco", "transfer_account_id": None},
    )
    store.set_merchant_memory(
        store.merchant_by_alias("Costco")["id"],
        categories_used={"cat-groc": 3, "cat-house": 1},
        processings={
            "-8421": {"parent_memo": "weekly", "splits": [{"category_id": "cat-groc", "amount_milliunits": -8421, "memo": ""}]},
            "-1500": {"parent_memo": "snack", "splits": [{"category_id": "cat-house", "amount_milliunits": -1500, "memo": ""}]},
        },
    )
    return store


@pytest.mark.asyncio
async def test_memory_screen_lists_merchants(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        await pilot.pause()
        # 1 merchant + 2 processing entries = 3 rows (when expanded by default)
        # Or 1 row (when collapsed). For Plan 3 we render flat: merchant header
        # row + entry rows beneath.
        assert screen.row_count() >= 1


@pytest.mark.asyncio
async def test_memory_screen_delete_entry(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        await pilot.pause()
        # Programmatically request deletion of the "-8421" entry on the Costco merchant.
        merchant_id = store.merchant_by_alias("Costco")["id"]
        screen.delete_entry(merchant_id, "-8421")
        await pilot.pause()
        # Check the store.
        m = store.merchant_by_alias("Costco")
        assert "-8421" not in m["processings"]
        assert "-1500" in m["processings"]


@pytest.mark.asyncio
async def test_memory_screen_reset_merchant(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.memory import MemoryScreen
    from textual.widgets import ContentSwitcher

    store = _seed_with_memory(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-memory"
        await pilot.pause()
        screen = app.query_one(MemoryScreen)
        screen.refresh_rows()
        merchant_id = store.merchant_by_alias("Costco")["id"]
        screen.reset_merchant(merchant_id)
        await pilot.pause()
        m = store.merchant_by_alias("Costco")
        assert m["processings"] == {}
        assert m["categories_used"] == {}
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_memory_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement MemoryScreen

Write `src/finab/tui/screens/memory.py`:

```python
"""MemoryScreen — sidebar entry #4.

Flat list view of merchants and their processings entries. Headers
are merchant rows; child rows are individual entries.

Actions:
  d — delete the highlighted processing entry (no-op on a header)
  R — reset all memory for the highlighted merchant (works on header or child)

For Plan 3, the UI is read-only-ish: deletion/reset works via the
public API (delete_entry, reset_merchant) but the keyboard wiring
of d/R from FinabApp is deferred to Task 10 (where we batch the
remaining bindings into FinabApp). Tests in this task exercise the
methods directly.
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
        # Row → (kind, merchant_id, amount_key_or_None).
        # Used so the d/R actions know what's under the cursor.
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
            lv.append(ListItem(Label(header), id=f"mem-header-{m['id']}"))
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
                lv.append(ListItem(Label(text), id=f"mem-entry-{m['id']}-{amt_key}"))
                self._row_map.append(("entry", m["id"], amt_key))

    def row_count(self) -> int:
        return len(self._row_map)

    def _current_row(self) -> Optional[tuple]:
        lv = self.query_one("#memory-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._row_map)):
            return None
        return self._row_map[idx]

    # ---- public API (used by tests + actions) ----

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
```

### Step 4: Mount MemoryScreen in FinabApp + bindings

Edit `src/finab/tui/app.py`. Import:

```python
from finab.tui.screens.memory import MemoryScreen
```

Update compose:

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    yield AccountsScreen(id="screen-accounts")
    yield MerchantsScreen(id="screen-merchants")
    yield MemoryScreen(id="screen-memory")
    for name, sid in SCREEN_IDS[4:]:  # skip first 4
        yield PlaceholderScreen(name, id=sid)
```

Add bind_data call in `_kickoff_load` and on_mount fallback:

```python
            memory_screen = self.query_one(MemoryScreen)
            memory_screen.bind_data(store=self._store)
```

Add the `d` and `R` bindings to FinabApp.BINDINGS:

```python
        ("d", "memory_delete", "Delete entry"),
        ("R", "memory_reset", "Reset merchant"),
```

Helper + delegating actions:

```python
    def _memory_screen_active(self) -> bool:
        switcher = self.query_one("#content-switcher", ContentSwitcher)
        return switcher.current == "screen-memory"

    def action_memory_delete(self) -> None:
        if self._memory_screen_active():
            self.query_one(MemoryScreen).action_delete()

    def action_memory_reset(self) -> None:
        if self._memory_screen_active():
            self.query_one(MemoryScreen).action_reset()
```

### Step 5: Style

Append to `src/finab/tui/styles.tcss`:

```tcss
MemoryScreen {
    width: 1fr;
    height: 1fr;
    padding: 1 2;
}

#memory-list {
    height: 1fr;
}
```

### Step 6: Run tests

Run: `uv run pytest tests/tui/test_memory_screen.py -v`
Expected: PASS — all 3 tests.

### Step 7: Run full suite

Run: `uv run pytest`
Expected: 227 passing.

### Step 8: Commit

```bash
git add src/finab/tui/screens/memory.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_memory_screen.py
git commit -m "feat(tui): MemoryScreen with delete-entry and reset-merchant"
```

---

## Task 10: Settings screen

Static-ish: shows budget id + a button to switch budget; .env file paths + button to reload; config.json / transactions.json paths.

For Plan 3, we keep it read-only. Switching budgets and reloading .env are bell-on-press; Plan 4 (or a follow-up) implements real switching.

**Files:**
- Create: `src/finab/tui/screens/settings.py`
- Modify: `src/finab/tui/app.py`
- Modify: `src/finab/tui/styles.tcss`
- Create: `tests/tui/test_settings_screen.py`

### Step 1: Write the failing test

Create `tests/tui/test_settings_screen.py`:

```python
import pytest


@pytest.mark.asyncio
async def test_settings_screen_shows_budget_id_and_paths(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.settings import SettingsScreen
    from textual.widgets import ContentSwitcher, Static

    app = FinabApp(budget_id="my-budget-id-1234")
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-settings"
        await pilot.pause()
        screen = app.query_one(SettingsScreen)
        # Aggregate all Static content into one string for the assertion.
        all_text = ""
        for s in screen.query(Static):
            all_text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        assert "my-budget-id-1234" in all_text
        assert "config.json" in all_text
        assert "transactions.json" in all_text
```

### Step 2: Run test to verify it fails

Run: `uv run pytest tests/tui/test_settings_screen.py -v`
Expected: FAIL — `ModuleNotFoundError`.

### Step 3: Implement SettingsScreen

Write `src/finab/tui/screens/settings.py`:

```python
"""SettingsScreen — sidebar entry #5.

Read-only display of:
  - Current budget id
  - Credential status (presence of YNAB_ACCESS_TOKEN, FINWISE_API_KEY)
  - Paths to config.json and transactions.json

Plan 3 makes this a static panel. Plan 4 / follow-up can add interactive
budget switching and .env reload.
"""
import os
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Static


class SettingsScreen(Container):
    """Sidebar entry #5 — settings + diagnostics."""

    def __init__(self, *, id: Optional[str] = None):
        super().__init__(id=id)
        self._budget_id: Optional[str] = None

    def bind_data(self, *, budget_id: Optional[str] = None) -> None:
        self._budget_id = budget_id
        self._render()

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-body"):
            yield Static("Settings", id="settings-title")
            yield Static("", id="settings-budget")
            yield Static("", id="settings-creds")
            yield Static("", id="settings-paths")

    def on_mount(self) -> None:
        self._render()

    def _render(self) -> None:
        # Budget
        try:
            self.query_one("#settings-budget", Static).update(
                f"  Budget ID:  {self._budget_id or '(not set)'}"
            )
        except Exception:
            return  # not mounted yet

        # Credentials
        ynab = "set" if os.environ.get("YNAB_ACCESS_TOKEN") else "MISSING"
        fw = "set" if os.environ.get("FINWISE_API_KEY") else "MISSING"
        self.query_one("#settings-creds", Static).update(
            f"  Credentials:\n"
            f"    YNAB_ACCESS_TOKEN: {ynab}\n"
            f"    FINWISE_API_KEY:   {fw}"
        )

        # Paths — pulled from the module-level constants in store/transactions.
        from finab.store import CONFIG_FILE
        from finab.transactions import TRANSACTIONS_FILE
        self.query_one("#settings-paths", Static).update(
            f"  State files:\n"
            f"    config.json:        {Path(CONFIG_FILE).resolve()}\n"
            f"    transactions.json:  {Path(TRANSACTIONS_FILE).resolve()}"
        )
```

### Step 4: Mount SettingsScreen in FinabApp

Edit `src/finab/tui/app.py`. Import:

```python
from finab.tui.screens.settings import SettingsScreen
```

Change compose loop (no more PlaceholderScreen — all five screens are real now):

```python
with ContentSwitcher(initial="screen-sync", id="content-switcher"):
    yield SyncScreen(id="screen-sync")
    yield AccountsScreen(id="screen-accounts")
    yield MerchantsScreen(id="screen-merchants")
    yield MemoryScreen(id="screen-memory")
    yield SettingsScreen(id="screen-settings")
```

Add a bind_data call for SettingsScreen at app mount time (it doesn't need fetched data — just the budget_id we already have):

In `on_mount`:

```python
    def on_mount(self) -> None:
        # Settings screen renders from local state — bind immediately.
        try:
            settings = self.query_one(SettingsScreen)
            settings.bind_data(budget_id=self._budget_id)
        except Exception:
            pass

        if self._fw_client and self._ynab_client and self._budget_id:
            self._kickoff_load()
        elif self._store is not None:
            try:
                self.query_one(AccountsScreen).bind_data(store=self._store)
                self.query_one(MerchantsScreen).bind_data(store=self._store)
                self.query_one(MemoryScreen).bind_data(store=self._store)
            except Exception:
                pass
```

Optional: remove the `PlaceholderScreen` import from `app.py` since it's no longer used.

### Step 5: Style

Append to `src/finab/tui/styles.tcss`:

```tcss
SettingsScreen {
    width: 1fr;
    height: 1fr;
    padding: 1 2;
}

#settings-body Static {
    padding: 1 0;
}

#settings-title {
    text-style: bold;
}
```

### Step 6: Run tests

Run: `uv run pytest tests/tui/test_settings_screen.py -v`
Expected: PASS.

### Step 7: Run full suite

Run: `uv run pytest`
Expected: 228 passing.

### Step 8: Commit

```bash
git add src/finab/tui/screens/settings.py src/finab/tui/app.py src/finab/tui/styles.tcss tests/tui/test_settings_screen.py
git commit -m "feat(tui): SettingsScreen with diagnostics panel"
```

---

## Task 11: Cutover — TUI becomes the default

Flip `main()` so that `uv run finab` launches the TUI by default. Add a `--classic` CLI flag that runs the old prompt-based flow for emergency fallback. The `FINAB_TUI` env var keeps working as a legacy alias.

**Files:**
- Modify: `src/finab/main.py`
- Modify: `tests/tui/test_app.py`

### Step 1: Write the failing tests

Append to `tests/tui/test_app.py`:

```python
def test_main_runs_tui_by_default(monkeypatch):
    """With no flag and no env var, main() launches FinabApp."""
    monkeypatch.delenv("FINAB_TUI", raising=False)
    launched = {"count": 0}

    class FakeApp:
        def __init__(self, **kwargs): pass
        def run(self): launched["count"] += 1

    import finab.tui.app as tui_app_mod
    monkeypatch.setattr(tui_app_mod, "FinabApp", FakeApp)
    # Stop the existing tests' sys.argv from being used — make sure no --classic.
    monkeypatch.setattr("sys.argv", ["finab"])

    from finab.main import main
    main()
    assert launched["count"] == 1


def test_main_classic_flag_runs_cli(monkeypatch):
    """--classic falls through to the old prompt flow."""
    monkeypatch.delenv("FINAB_TUI", raising=False)
    monkeypatch.setattr("sys.argv", ["finab", "--classic"])

    # Don't let FinabApp construct.
    import finab.tui.app as tui_app_mod
    class ExplodingApp:
        def __init__(self, **kwargs):
            raise AssertionError("FinabApp should not be constructed when --classic is passed")
    monkeypatch.setattr(tui_app_mod, "FinabApp", ExplodingApp)

    # Short-circuit the CLI's YNAB init so we exit fast.
    import finab.main as main_mod
    class FakeYnabClient:
        def __init__(self): raise RuntimeError("stop here")
    monkeypatch.setattr(main_mod, "YNABClient", FakeYnabClient)

    from finab.main import main
    main()  # should print error and return without crashing
```

### Step 2: Run tests to verify they fail

Run: `uv run pytest tests/tui/test_app.py::test_main_runs_tui_by_default tests/tui/test_app.py::test_main_classic_flag_runs_cli -v`
Expected: FAIL — `main()` currently only launches TUI when `FINAB_TUI` is set.

### Step 3: Rewrite the dispatch in main()

Edit `src/finab/main.py`. Replace the current dispatch top-of-`main()` with:

```python
def main():
    import os
    import sys

    # New default: TUI. Opt out with --classic. Legacy FINAB_TUI=1 still
    # routes through here so existing muscle memory keeps working.
    use_classic = "--classic" in sys.argv
    if not use_classic:
        load_dotenv()
        from finab.transactions import TransactionsStore
        from finab.tui.app import FinabApp
        FinabApp(
            fw_client=FinWiseClient(),
            ynab_client=YNABClient(),
            budget_id=load_budget_id(),
            store=ConfigStore(),
            tx_store=TransactionsStore(),
        ).run()
        return

    # --- classic CLI flow below (unchanged) ---
    load_dotenv()
    # (existing body continues here — print("Hello from finab!"), the
    #  budget selection prompts, the three sync_* calls)
    ...
```

Remove the `os.environ.get("FINAB_TUI")` check since the TUI is now default. If the FINAB_TUI env var is set, the user is using legacy muscle memory — that still works because `use_classic` is `False` so we hit the TUI path.

Update the existing `test_main_launches_tui_when_flag_set` test (from Plan 2) — it asserted that FINAB_TUI=1 triggers the TUI. That still passes after this rewrite because `--classic` ISN'T in argv. But re-read the test to confirm it doesn't rely on the env-var-specific code path:

```python
def test_main_launches_tui_when_flag_set(monkeypatch):
    """LEGACY: FINAB_TUI=1 → TUI. Still works post-cutover because TUI is default."""
    monkeypatch.setenv("FINAB_TUI", "1")
    ...
```

It should still pass — `FINAB_TUI=1` is set but the relevant code path is just "no --classic → TUI". Verify the existing tests stay green.

Also the existing `test_main_falls_through_to_cli_when_flag_unset` from Plan 2 needs update — its assertion was that FINAB_TUI unset → CLI, but now CLI requires `--classic`. Update or delete it. Recommendation: rename it `test_main_classic_falls_through_to_cli` and verify the `--classic` behavior. The new `test_main_classic_flag_runs_cli` test already covers this, so delete the old `test_main_falls_through_to_cli_when_flag_unset`.

### Step 4: Run tests

Run: `uv run pytest tests/tui/test_app.py -v`
Expected: PASS — including the 2 new tests.

### Step 5: Run full suite

Run: `uv run pytest`
Expected: 229 passing (228 prior + 2 new - 1 deleted).

### Step 6: Manual sanity (skip if no terminal)

Run `uv run finab` (with FINAB_TUI unset). It should now launch the TUI by default. Press `q` to exit. If the app fails to start, debug before continuing.

Run `uv run finab --classic`. It should run the old prompt-based flow.

### Step 7: Commit

```bash
git add src/finab/main.py tests/tui/test_app.py
git commit -m "feat(main): TUI is now the default; --classic runs the old CLI"
```

---

## Task 12: Cleanup — remove the old prompt code and --classic flag

The TUI has been the default for one task's worth of commits. Time to delete the old code. This task removes:

- The `--classic` flag and the entire CLI flow inside `main()`
- The interactive prompt helpers in `transactions.py` (`_pick_category`, `_collect_splits`, etc.)
- `_PendingQueue`, `_process_one_transaction`, `sync_transactions` from `transactions.py`
- The interactive prompt helpers in `main.py` (`_prompt_alias_*`, `_interactive_pick`)
- Obsolete tests in `tests/test_main.py` (if it exists) and `tests/test_sync_transactions.py` that exercise the removed code
- The ANSI color helpers in `transactions.py` and `main.py` (now unused)

**Files:**
- Modify: `src/finab/main.py` (gut)
- Modify: `src/finab/transactions.py` (gut to: TransactionsStore + re-exports only)
- Delete or trim: `tests/test_main.py` (if any), `tests/test_sync_transactions.py`, `tests/test_transactions.py` (tests for removed functions only — keep tests for `merge_and_filter_transactions` and other helpers since those moved to engine and re-exports preserve the import path)

### Step 1: Inventory what to delete

First, read the current files to confirm what's there:

```bash
grep -nE "^def |^class " src/finab/main.py
grep -nE "^def |^class " src/finab/transactions.py
```

Expected output (paraphrased):

`main.py` — keep: re-exports (engine.accounts, engine.merchants), module-top imports, `main()`. Delete: `_color`, `_bold`, `_dim`, `_red`, `_green`, `_yellow`, `_cyan`, `_prompt_alias_required`, `_prompt_yes_no`, `_gather_pickable_entries`, `_interactive_pick`, `_prompt_alias_with_picker`, `sync_accounts`, `sync_merchants`.

`transactions.py` — keep: `TransactionsStore`, `TRANSACTIONS_FILE`, the re-export block from engine.sync. Delete: ANSI helpers `_color/_bold/_dim/_green/_cyan/_yellow`, interactive helpers `_pick_category`, `_pick_category_from_full_list`, `_create_new_category`, `_prompt_memo`, `_collect_splits`, `_pick_from_processings`, `_confirm`, `_PendingQueue`, `_process_one_transaction`, `sync_transactions`.

### Step 2: Trim transactions.py

Open `src/finab/transactions.py`. Read it through to find the boundaries. The retained content:

```python
"""TransactionsStore — owns transactions.json, the map from FinWise
transaction UUIDs to our durable YNAB import_id.

The interactive prompt code that used to live in this module was
removed when the TUI became the default (Plan 3 cutover). The pure
helpers were already moved to finab.engine.sync (Plan 1).
"""
import json
import os
from pathlib import Path
from typing import Optional


TRANSACTIONS_FILE = Path("transactions.json")


# --- Re-exports from finab.engine.sync ---
from finab.engine.sync import (
    _INFLOW_CATEGORY_NAMES,
    _TRACKING_ACCOUNT_TYPES,
    _account_is_tracking,
    _is_inflow,
    _is_before_current_month,
    _is_transfer,
    _find_inflow_category,
    _closest_processing,
    _apply_repeat,
    _apply_processing_to_txn,
    _update_merchant_memory,
    _category_name,
    _render_splits,
    _sort_key,
    merge_and_filter_transactions,
)


class TransactionsStore:
    # (existing implementation unchanged — see current file lines ~22-88)
    ...
```

Delete everything else from the file. The retained file is ~100 lines (down from ~750).

### Step 3: Trim main.py

Open `src/finab/main.py`. Retain only the module-top imports, the re-export blocks (`engine.accounts` and `engine.merchants`), and `main()`.

Rewrite `main()` to be TUI-only:

```python
from dotenv import load_dotenv
from finab.client import FinWiseClient
from finab.ynab_client import YNABClient
from finab.config import load_budget_id, save_budget_id
from finab.store import ConfigStore


# --- Re-exports from finab.engine.accounts ---
from finab.engine.accounts import (
    _calculate_starting_balance,
    _account_with_overrides,
    _reconcile_store_accounts_to_ynab,
)

# --- Re-exports from finab.engine.merchants ---
from finab.engine.merchants import (
    _link_account_transfer_payee,
    _extract_distinct_merchants,
    _reconcile_store_merchants_to_ynab,
    _record_merchant_alias,
)


def main():
    load_dotenv()
    from finab.transactions import TransactionsStore
    from finab.tui.app import FinabApp
    FinabApp(
        fw_client=FinWiseClient(),
        ynab_client=YNABClient(),
        budget_id=load_budget_id(),
        store=ConfigStore(),
        tx_store=TransactionsStore(),
    ).run()


if __name__ == "__main__":
    main()
```

That's it — `main.py` is now ~30 lines.

### Step 4: Trim obsolete tests

Existing test files that test the removed code:

- `tests/test_main.py` (if it exists) — delete entirely.
- `tests/test_sync_transactions.py` — this exercises `sync_transactions` which no longer exists. Delete entirely.
- `tests/test_transactions.py` — has tests for `_pick_category`, `_collect_splits`, `_process_one_transaction`, etc. Delete those tests but keep tests for `merge_and_filter_transactions`, `_closest_processing`, `_apply_processing_to_txn`, `_render_splits` etc. (those moved to engine; the re-export path still works).
- `tests/test_sync_accounts.py` — exercises `sync_accounts` (deleted). Delete this file too.
- `tests/test_sync_merchants.py` — exercises `sync_merchants` (deleted). Delete.
- `tests/tui/test_app.py` — the `test_main_classic_flag_runs_cli` test from Task 11 needs to be deleted, since `--classic` is gone. The `test_main_runs_tui_by_default` test is still good.
- `tests/tui/test_app.py::test_main_launches_tui_when_flag_set` — legacy. Delete; it's redundant with `test_main_runs_tui_by_default`.

Concrete approach:

```bash
# Delete obsolete test files entirely
rm tests/test_sync_transactions.py
rm tests/test_sync_accounts.py
rm tests/test_sync_merchants.py
# tests/test_main.py — check existence
ls tests/test_main.py 2>/dev/null && rm tests/test_main.py
```

For `tests/test_transactions.py`, open it and remove tests targeting the deleted prompt functions. Keep:
- `TestMergeAndFilter` and any other behavioural tests for `merge_and_filter_transactions`
- Tests for `_closest_processing`, `_apply_processing_to_txn`, `_render_splits`, `_update_merchant_memory`, `_is_before_current_month`, etc. — these helpers are now in `engine/sync.py` and re-exported.

Remove:
- Tests importing `_pick_category`, `_pick_category_from_full_list`, `_create_new_category`, `_prompt_memo`, `_collect_splits`, `_pick_from_processings`, `_confirm`, `_PendingQueue`, `_process_one_transaction`.

The grep at Step 1 of Task 12 already gives the imports list — use it as a checklist.

For `tests/tui/test_app.py`, delete:
- `test_main_launches_tui_when_flag_set`
- `test_main_classic_flag_runs_cli`

Keep `test_main_runs_tui_by_default` (and rename if helpful).

### Step 5: Update tests/tui/test_app.py main test

`test_main_runs_tui_by_default` already works without `--classic`. Make sure it remains correct:

```python
def test_main_runs_tui_by_default(monkeypatch):
    """main() launches FinabApp unconditionally now."""
    launched = {"count": 0}

    class FakeApp:
        def __init__(self, **kwargs): pass
        def run(self): launched["count"] += 1

    import finab.tui.app as tui_app_mod
    monkeypatch.setattr(tui_app_mod, "FinabApp", FakeApp)
    monkeypatch.setattr("sys.argv", ["finab"])

    from finab.main import main
    main()
    assert launched["count"] == 1
```

### Step 6: Run the full suite

Run: `uv run pytest`
Expected: some count less than 228 (because we deleted obsolete tests). The remaining tests should all pass. Report the new count.

If any test fails because it imported a now-deleted symbol, decide:
- Test exercises behavior that still exists (via re-export) → keep, fix import to use `finab.transactions.X` (which re-exports from `engine.sync`)
- Test exercises deleted behavior → delete the test

### Step 7: Manual smoke (skip if no terminal)

Run `uv run finab`. The TUI should launch. Sidebar shows 5 real screens. `q` to quit (with confirm if pending).

### Step 8: Boundary checks

Run: `grep -rn "input(" src/finab/ || echo "no input() calls"`
Expected: zero `input(` calls in the entire `src/finab/` tree. (The TUI doesn't use `input()`; the engine never did.)

Run: `grep -rn "_PendingQueue\|_process_one_transaction\|sync_transactions" src/ tests/`
Expected: zero matches. Those names are gone.

Run: `grep -rn "FINAB_TUI" src/finab/`
Expected: zero matches. The env-var dispatch is gone too.

### Step 9: Commit

```bash
git add -A
git commit -m "refactor: remove old prompt-based CLI; TUI is now the only entrypoint"
```

---

## Task 13: Final verification

End-to-end sanity check across the whole TUI migration. No code changes.

- [ ] **Step 1: Full test suite**

Run: `uv run pytest -v 2>&1 | tail -60`
Expected: every test passes, no failures. Report the final total count.

- [ ] **Step 2: Engine boundary check**

Run: `grep -rn "input(" src/finab/engine/ || echo "OK no input()"` 
Run: `grep -rn "^from textual\|^import textual" src/finab/engine/ || echo "OK no textual"`
Both should report no matches.

- [ ] **Step 3: Old prompt code is gone**

Run: `grep -rn "_PendingQueue\|_process_one_transaction\|sync_transactions\|_pick_category\|_collect_splits\|_prompt_alias\|sync_accounts\|sync_merchants" src/ tests/ || echo "OK all removed"`
Expected: no matches (or only matches inside docstring/historical comments).

- [ ] **Step 4: Module structure**

Run: `find src/finab tests -type f \( -name '*.py' -o -name '*.tcss' \) | sort`
Expected files list — verify all the screens/widgets created across Plans 2 and 3 are present.

- [ ] **Step 5: Smoke import check**

```bash
uv run python -c "from finab.main import main; print('OK')"
uv run python -c "from finab.tui.app import FinabApp; print('OK')"
uv run python -c "from finab.engine.sync import SyncEngine, Candidate; print('OK')"
uv run python -c "from finab.store import ConfigStore; print('OK')"
```

All should print `OK`.

- [ ] **Step 6: TUI launch smoke (if a terminal is available)**

Run: `uv run finab`
Expected: TUI starts, sidebar shows 5 screens (Sync, Accounts, Merchants, Memory, Settings); pressing `q` exits cleanly. If pending data is required (real YNAB/FW credentials), the data load may fail and the error banner should appear — that's still success for "the app boots".

- [ ] **Step 7: Commit log**

Run: `git log --oneline f9d049a..HEAD`
(f9d049a was the last commit of Plan 2 — verify by checking the message.)

Expected: ~13-15 commits for Plan 3 tasks plus any review-fix commits.

No code changes in this task. Verification only.

---

## Self-Review

**Spec coverage:**
- Spec §Sync screen — fully implemented (Plans 2 + 3).
- Spec §Status glyphs — ⚠ now wired (Task 3). All 7 glyphs covered.
- Spec §Keybindings — `c/s/r/t/u/f/q/Enter/g/G/?` all bound (Tasks 3, 4, 5 + Plan 2 Tasks 13, 14). `j/k` work via ListView defaults.
- Spec §Other screens — Accounts/Merchants/Memory/Settings all implemented (Tasks 7, 8, 9, 10). Relink actions are deferred (bell-on-press); documented as known-gap, Plan 4 follow-up.
- Spec §Data flow & persistence — `_kickoff_load` populates all 5 screens (Tasks 7-10 each add a bind_data call). Error path surfaces via ErrorBanner (Task 6).
- Spec §Error handling — fetch failures: ErrorBanner (Task 6); flush failures: bell in SyncScreen (Plan 2, retained); Ctrl+C / q: FlushConfirmModal (Task 4); inflow missing: candidate stays pending (engine, unchanged); FW transfer-but-not-linked: ⚠ glyph + warning text (Task 1 + 3).
- Spec §Testing — engine + TUI tests added per task. ~50 net new tests across Plan 3.
- Spec §Migration plan step 5 — covered Tasks 3, 7, 8, 9, 10.
- Spec §Migration plan step 6 — Task 11.
- Spec §Migration plan step 7 — Task 12.

**Known gaps deferred to Plan 4 / follow-up:**
- `action_relink` on AccountsScreen and MerchantsScreen are bells. Proper implementation needs a picker over fetched YNAB-side data (not the store), which would require either passing the loaded YNAB accounts/payees through `bind_data` or refactoring AccountLinkPicker to accept arbitrary sources.
- Budget switching in SettingsScreen is bell-on-press. Needs a picker over `YNABClient.get_budgets()`.
- `.env` reload in SettingsScreen — not implemented.
- "Create new YNAB account" from AccountsScreen — not implemented.
- The split editor's command-line UX (inherited from Plan 2) is still command-line. A DataTable upgrade is fair game later.
- `_reconcile_*` functions in engine/accounts.py and engine/merchants.py still have print() calls — flagged with TODO(plan-2) markers. With the CLI gone, these prints are dead (no CLI path reaches them in Plan 3+). They're still callable from tests, but the TODO is now technically referring to Plan 4+. Either leave the comments and remove later, or simply delete the print() calls since they're never seen by the user. Recommend: leave as TODO until Plan 4 decides.

**Placeholder scan:**
- No "TBD" / "fill in details" / "implement later" in step bodies.
- "Plan 4" / "follow-up" references in code comments — these are legitimate future-work markers, NOT plan placeholders (the work is not in this plan's scope by design).

**Type consistency:**
- `Candidate.warnings: list[str]` everywhere.
- `SyncEngine.apply_history(candidate_id, *, entry: dict)` — signature consistent in Task 1 impl, Task 3 callsites.
- ConfigStore methods (`set_account_alias`, `set_account_ignore`, `set_merchant_alias`, `delete_processing_entry`, `reset_merchant_memory`) — names consistent across Task 2 impl and Tasks 7, 8, 9 callsites.
- `AccountLinkPicker(store, *, title, value_kind)` — Task 6 introduces with `value_kind="transfer_payee_id"` default; Task 3's action_force_transfer relies on the default; Task 7 mentions but defers actual relink usage.
- Screen `bind_data` signatures vary intentionally: `SyncScreen.bind_data(loaded, store, tx_store)`, `AccountsScreen.bind_data(store)`, etc. — each takes what it needs.

**Files NOT touched in Plan 3** (sanity check): engine/sync.py only gets `Candidate.warnings` + `apply_history` additions; engine/accounts.py, engine/merchants.py, client.py, ynab_client.py, models.py, config.py untouched. Tests in `tests/engine/` untouched except for `test_sync_engine.py` getting new test classes.

---
