"""Tests for SplitEditorModal — focus on the result it dismisses with."""
import pytest


class _FakeCategory:
    def __init__(self, id, name):
        self.id = id
        self.name = name
        self.hidden = False
        self.deleted = False


@pytest.mark.asyncio
async def test_split_editor_initial_state():
    """A fresh modal shows one row holding the full transaction amount."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                SplitEditorModal(
                    txn_amount=-84210,  # milliunits
                    categories=[_FakeCategory("cat-x", "Generic")],
                    used_categories={},
                    merchant_alias="Costco",
                ),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        rows = modal.current_rows()
        assert len(rows) == 1
        assert rows[0]["amount"] == -84210
        assert modal.remaining_milliunits() == 0


@pytest.mark.asyncio
async def test_split_editor_dismisses_with_splits_when_balanced():
    """Programmatically populate rows, confirm via ctrl+s, get back the splits."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = SplitEditorModal(
                txn_amount=-80000,  # milliunits = -80.00
                categories=[_FakeCategory("cat-a", "A"), _FakeCategory("cat-b", "B")],
                used_categories={},
                merchant_alias="?",
            )
            modal.set_rows([
                {"amount": -50000, "category_id": "cat-a", "memo": "a"},
                {"amount": -30000, "category_id": "cat-b", "memo": "b"},
            ])
            self.push_screen(modal, callback=lambda r: result_holder.__setitem__("value", r))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert result_holder["value"] == [
        {"amount": -50000, "category_id": "cat-a", "memo": "a"},
        {"amount": -30000, "category_id": "cat-b", "memo": "b"},
    ]


@pytest.mark.asyncio
async def test_split_editor_refuses_confirm_when_unbalanced():
    """Sum != total → ctrl+s is a no-op (modal stays open, callback not invoked)."""
    from textual.app import App
    from finab.tui.widgets.split_editor import SplitEditorModal

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            modal = SplitEditorModal(
                txn_amount=-80000,
                categories=[_FakeCategory("cat-a", "A")],
                used_categories={},
                merchant_alias="?",
            )
            modal.set_rows([
                {"amount": -30000, "category_id": "cat-a", "memo": ""},
            ])
            self.push_screen(modal, callback=lambda r: result_holder.__setitem__("value", r))

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        # Modal should still be open (callback not invoked).
        assert result_holder["value"] == "not-set"
        modal = app.screen
        assert modal.remaining_milliunits() != 0
