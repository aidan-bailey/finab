"""Tests for YnabAccountPicker — a modal over the *fetched* YNAB accounts.

Different from AccountLinkPicker (which scans the store). This picker is
used during the mapping flow when the user wants to link a new FW account
to an existing YNAB account.
"""
import pytest


class _FakeYnabAccount:
    """Stub matching the YNAB SDK Account shape — only fields the picker reads."""
    def __init__(self, id, name, type="checking", deleted=False, closed=False):
        self.id = id
        self.name = name
        self.type = type
        self.deleted = deleted
        self.closed = closed


@pytest.mark.asyncio
async def test_picker_dismisses_with_chosen_account():
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    accounts = [
        _FakeYnabAccount("yn-a", "Chase Checking"),
        _FakeYnabAccount("yn-b", "Emergency Fund", type="savings"),
    ]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabAccountPicker(ynab_accounts=accounts, title="Pick a YNAB account"),
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
    assert result_holder["value"] in {"yn-a", "yn-b"}


@pytest.mark.asyncio
async def test_picker_filters_out_deleted_and_closed():
    """Deleted/closed YNAB accounts are not selectable."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    accounts = [
        _FakeYnabAccount("yn-a", "Visible"),
        _FakeYnabAccount("yn-b", "Deleted", deleted=True),
        _FakeYnabAccount("yn-c", "Closed", closed=True),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(YnabAccountPicker(ynab_accounts=accounts, title="Pick"))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        assert ol.option_count == 1


@pytest.mark.asyncio
async def test_picker_escape_dismisses_with_none():
    from textual.app import App
    from finab.tui.widgets.ynab_account_picker import YnabAccountPicker

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YnabAccountPicker(ynab_accounts=[_FakeYnabAccount("yn-a", "A")], title="Pick"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
