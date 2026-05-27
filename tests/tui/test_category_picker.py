"""Tests for the CategoryPickerModal."""
import pytest


class _FakeCategory:
    def __init__(self, id, name, *, hidden=False, deleted=False):
        self.id = id
        self.name = name
        self.hidden = hidden
        self.deleted = deleted


@pytest.mark.asyncio
async def test_category_picker_dismisses_with_selected_id():
    """Picking the first row dismisses with that category's id."""
    from textual.app import App
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [
        _FakeCategory("cat-groc", "Groceries"),
        _FakeCategory("cat-house", "Household"),
    ]
    used = {"cat-groc": 18}

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = CategoryPickerModal(
                categories=categories,
                used_categories=used,
                merchant_alias="Costco",
            )
            self.push_screen(modal, callback=self._on_dismiss)

        def _on_dismiss(self, result):
            result_holder["value"] = result

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # The OptionList must have focus before Enter activates a selection
        # — focus it explicitly so Enter goes to the OptionList, not the Input.
        from textual.widgets import OptionList
        ol = app.screen.query_one(OptionList)
        ol.focus()
        await pilot.pause()
        # First row is the most-used (Groceries). Press Enter on it.
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder["value"] == "cat-groc"


@pytest.mark.asyncio
async def test_category_picker_dismisses_with_none_on_escape():
    from textual.app import App
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [_FakeCategory("cat-groc", "Groceries")]
    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                CategoryPickerModal(categories=categories, used_categories={}, merchant_alias="?"),
                callback=lambda r: result_holder.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder["value"] is None


@pytest.mark.asyncio
async def test_category_picker_filters_on_input():
    """Typing in the input narrows the OptionList to matching categories."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.tui.widgets.category_picker import CategoryPickerModal

    categories = [
        _FakeCategory("cat-groc", "Groceries"),
        _FakeCategory("cat-house", "Household"),
        _FakeCategory("cat-gas", "Gas"),
    ]

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                CategoryPickerModal(categories=categories, used_categories={}, merchant_alias="?"),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Type into the input (it should have focus by default after on_mount).
        await pilot.press("g", "r", "o")
        await pilot.pause()
        ol = app.screen.query_one(OptionList)
        # After filter, the only remaining option should match "gro" — Groceries.
        # OptionList.option_count gives total loaded options after filter refresh.
        assert ol.option_count == 1
