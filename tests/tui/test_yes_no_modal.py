"""Tests for YesNoModal — a 2-button yes/no confirm."""
import pytest


@pytest.mark.asyncio
async def test_yes_returns_true():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="Proceed?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert result["value"] is True


@pytest.mark.asyncio
async def test_no_returns_false():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
    assert result["value"] is False


@pytest.mark.asyncio
async def test_escape_returns_none():
    from textual.app import App
    from finab.tui.widgets.yes_no_modal import YesNoModal

    result = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                YesNoModal(message="?"),
                callback=lambda r: result.__setitem__("value", r),
            )

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result["value"] is None
