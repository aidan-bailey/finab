"""Tests for YnabPayeePicker — fuzzy search over fetched YNAB payees."""
import pytest


class _FakePayee:
    def __init__(self, id, name, deleted=False, transfer_account_id=None):
        self.id = id
        self.name = name
        self.deleted = deleted
        self.transfer_account_id = transfer_account_id


@pytest.mark.asyncio
async def test_picker_dismisses_with_payee_id():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    payees = [_FakePayee("yn-pa", "Costco"), _FakePayee("yn-pb", "Amazon")]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabPayeePicker(ynab_payees=payees, title="Pick payee"),
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
    assert result_holder["value"] in {"yn-pa", "yn-pb"}


@pytest.mark.asyncio
async def test_picker_filters_out_transfer_payees_by_default():
    """Transfer payees (those with transfer_account_id set) are payees for
    YNAB's own accounts — they shouldn't be linkable as regular merchant payees.
    The picker hides them by default."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    payees = [
        _FakePayee("yn-pa", "Costco"),
        _FakePayee("yn-pb", "Transfer: Savings", transfer_account_id="yn-sav"),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(YnabPayeePicker(ynab_payees=payees, title="Pick"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 1


@pytest.mark.asyncio
async def test_picker_escape_dismisses_with_none():
    from textual.app import App
    from finab.tui.widgets.ynab_payee_picker import YnabPayeePicker

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabPayeePicker(ynab_payees=[_FakePayee("yn-pa", "A")], title="Pick"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
