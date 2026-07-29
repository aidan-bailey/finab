"""Tests for the first-run setup wizard wired into FinabApp.

Covers: trigger on missing budget_id, budget pick → save + advance,
strict accounts gate, optional merchants step, finish, navigation lock,
and the no-wizard path when a budget_id already exists.
"""
import json

import pytest

from finab.store import ConfigStore
from finab.transactions import TransactionsStore


# --- fakes -------------------------------------------------------------

class _FakeBudget:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class _FakeFwAcct:
    def __init__(self, finwise_id, name, type="checking"):
        self.finwise_id = finwise_id
        self.name = name
        self.type = type
        self.balance = 0
        self.currency_code = "USD"


class _FakeFwClient:
    def __init__(self, accounts=None, txns=None):
        self._a = accounts or []
        self._t = txns or []

    def get_accounts(self):
        return self._a

    def get_transactions(self, **kwargs):
        return self._t


class _FakeYnabClient:
    def __init__(self, budgets=None, accounts=None, payees=None):
        self._budgets = budgets or []
        self._accounts = accounts or []
        self._payees = payees or []

    def get_budgets(self):
        return self._budgets

    def get_accounts(self, budget_id):
        return self._accounts

    def get_transactions(self, budget_id):
        return []

    def get_categories(self, budget_id):
        return []

    def get_category_groups_with_categories(self, budget_id):
        return []

    def get_payees(self, budget_id):
        return self._payees


def _app(tmp_path, *, fw_client=None, ynab_client=None, budget_id=None):
    from finab.tui.app import FinabApp
    return FinabApp(
        fw_client=fw_client,
        ynab_client=ynab_client,
        budget_id=budget_id,
        store=ConfigStore(tmp_path / "config.json"),
        tx_store=TransactionsStore(tmp_path / "transactions.json"),
    )


# --- trigger -----------------------------------------------------------

@pytest.mark.asyncio
async def test_wizard_starts_when_no_budget_id(tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    fw = _FakeFwClient()
    yn = _FakeYnabClient(budgets=[_FakeBudget("b-1", "Personal")])
    app = _app(tmp_path, fw_client=fw, ynab_client=yn, budget_id=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, BudgetPickerModal)


@pytest.mark.asyncio
async def test_no_wizard_when_budget_id_present(tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal
    from textual.widgets import ContentSwitcher

    fw = _FakeFwClient()
    yn = _FakeYnabClient(budgets=[_FakeBudget("b-1", "Personal")])
    app = _app(tmp_path, fw_client=fw, ynab_client=yn, budget_id="b-existing")
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, BudgetPickerModal)
        assert app._wizard_step is None
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        assert switcher.current == "screen-sync"


# --- budget pick → save + advance -------------------------------------

@pytest.mark.asyncio
async def test_picking_budget_saves_and_enters_accounts_step(tmp_path):
    from textual.widgets import ContentSwitcher
    from finab.tui.widgets.budget_picker import BudgetPickerModal
    from finab.tui.widgets.wizard_banner import WizardBanner

    fw = _FakeFwClient(accounts=[_FakeFwAcct("fw-a", "Chase")])
    yn = _FakeYnabClient(budgets=[_FakeBudget("b-1", "Personal")])
    app = _app(tmp_path, fw_client=fw, ynab_client=yn, budget_id=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, BudgetPickerModal)
        app.screen.dismiss("b-1")
        await pilot.pause()
        # budget persisted via the store (coherent with accounts/merchants)
        data = json.loads((tmp_path / "config.json").read_text())
        assert data["budget_id"] == "b-1"
        # advanced to accounts step
        assert app._wizard_step == "accounts"
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        assert switcher.current == "screen-accounts"
        assert app.query_one(WizardBanner).has_class("active")


@pytest.mark.asyncio
async def test_cancelling_budget_does_not_save(tmp_path):
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    fw = _FakeFwClient()
    yn = _FakeYnabClient(budgets=[_FakeBudget("b-1", "Personal")])
    app = _app(tmp_path, fw_client=fw, ynab_client=yn, budget_id=None)
    exited = {"v": False}
    async with app.run_test() as pilot:
        await pilot.pause()
        assert isinstance(app.screen, BudgetPickerModal)
        app.exit = lambda *a, **k: exited.__setitem__("v", True)
        await pilot.press("escape")
        await pilot.pause()
        # no budget written
        cfg = tmp_path / "config.json"
        if cfg.exists():
            assert "budget_id" not in json.loads(cfg.read_text())
        assert exited["v"] is True


# --- accounts gate (strict) -------------------------------------------

@pytest.mark.asyncio
async def test_accounts_gate_blocks_until_all_mapped(tmp_path):
    from textual.widgets import ContentSwitcher
    from finab.tui.screens.accounts import AccountsScreen

    store = ConfigStore(tmp_path / "config.json")
    from finab.tui.app import FinabApp
    app = FinabApp(store=store)  # no clients → no auto-wizard
    async with app.run_test() as pilot:
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(
            store=store,
            fw_accounts=[_FakeFwAcct("fw-a", "Chase")],  # one unmapped
            ynab_accounts=[],
            ynab_client=None,
            budget_id="b",
        )
        await pilot.pause()
        app._enter_accounts_step()
        await pilot.pause()
        assert app._wizard_step == "accounts"

        # Gate blocks: one unmapped account remains.
        app.action_wizard_next()
        await pilot.pause()
        assert app._wizard_step == "accounts"

        # Map it, then the gate opens.
        store.add_account(
            alias="Chase",
            fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
            ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "tp"},
        )
        ac.refresh_rows()
        assert ac.unmapped_count() == 0
        app.action_wizard_next()
        await pilot.pause()
        assert app._wizard_step == "merchants"
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        assert switcher.current == "screen-merchants"


# --- merchants step (optional) + finish -------------------------------

@pytest.mark.asyncio
async def test_merchants_next_finishes_to_sync(tmp_path):
    from textual.widgets import ContentSwitcher
    from finab.tui.widgets.wizard_banner import WizardBanner

    store = ConfigStore(tmp_path / "config.json")
    from finab.tui.app import FinabApp
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._enter_merchants_step()
        await pilot.pause()
        assert app._wizard_step == "merchants"
        # Next on merchants finishes regardless of unmapped count.
        app.action_wizard_next()
        await pilot.pause()
        assert app._wizard_step is None
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        assert switcher.current == "screen-sync"
        assert not app.query_one(WizardBanner).has_class("active")


# --- navigation lock ---------------------------------------------------

@pytest.mark.asyncio
async def test_sidebar_locked_during_wizard(tmp_path):
    from textual.widgets import ContentSwitcher, ListView

    store = ConfigStore(tmp_path / "config.json")
    from finab.tui.app import FinabApp
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._enter_accounts_step()
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        assert switcher.current == "screen-accounts"
        # Try to jump the sidebar to Merchants (index 2).
        sidebar = app.query_one("#sidebar", ListView)
        sidebar.index = 2
        await pilot.pause()
        # Nav-lock snaps the content back to the wizard's current step.
        assert switcher.current == "screen-accounts"
