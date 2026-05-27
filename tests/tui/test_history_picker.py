"""Tests for HistoryPickerModal."""
import pytest


@pytest.mark.asyncio
async def test_history_picker_dismisses_with_chosen_entry():
    """Pressing Enter on the highlighted (closest) row returns its (amount_key, entry) tuple."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.history_picker import HistoryPickerModal

    processings = {
        "-84210": {"parent_memo": "weekly", "splits": [
            {"category_id": "cat-groc", "amount_milliunits": -84210, "memo": ""}
        ]},
        "-12500": {"parent_memo": "gas", "splits": [
            {"category_id": "cat-gas", "amount_milliunits": -12500, "memo": ""}
        ]},
    }
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                HistoryPickerModal(processings=processings, txn_amount=-80000),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The OptionList needs focus for Enter to trigger selection.
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    # closest to -80000 is -84210 (diff 4210) vs -12500 (diff 67500) → -84210 wins.
    assert result_holder["value"] is not None
    amount_key, entry = result_holder["value"]
    assert amount_key == "-84210"
    assert entry["parent_memo"] == "weekly"


@pytest.mark.asyncio
async def test_history_picker_dismisses_with_none_on_escape():
    from textual.app import App
    from finab.tui.widgets.history_picker import HistoryPickerModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                HistoryPickerModal(processings={"-100": {"parent_memo": "", "splits": []}}, txn_amount=-100),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None
