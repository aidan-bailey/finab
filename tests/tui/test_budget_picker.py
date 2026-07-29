"""Tests for BudgetPickerModal — a modal over the YNAB budgets (plans)
returned by ynab_client.get_budgets(). Used by the first-run wizard.

Dismisses with the chosen budget id (str) or None on cancel.
"""
import pytest


class _FakeBudget:
    """Stub matching the YNAB plan/budget summary shape the picker reads."""
    def __init__(self, id, name):
        self.id = id
        self.name = name


@pytest.mark.asyncio
async def test_picker_builds_one_option_per_budget():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]

    class _Host(App):
        def on_mount(self):
            self.push_screen(BudgetPickerModal(budgets=budgets))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 2


@pytest.mark.asyncio
async def test_picker_dismisses_with_chosen_budget_id():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    budgets = [_FakeBudget("b-1", "Personal"), _FakeBudget("b-2", "Business")]
    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                BudgetPickerModal(budgets=budgets),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert result["value"] in {"b-1", "b-2"}


@pytest.mark.asyncio
async def test_picker_escape_dismisses_with_none():
    from textual.app import App
    from finab.tui.widgets.budget_picker import BudgetPickerModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                BudgetPickerModal(budgets=[_FakeBudget("b-1", "Personal")]),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result["value"] is None
