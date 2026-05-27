"""Tests for the FlushConfirmModal."""
import pytest


@pytest.mark.asyncio
async def test_flush_confirm_yes_dismisses_with_flush():
    """Pressing y dismisses with 'flush'."""
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
async def test_flush_confirm_no_dismisses_with_skip():
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
async def test_flush_confirm_escape_dismisses_with_cancel():
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
