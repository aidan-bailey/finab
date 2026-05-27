import pytest


@pytest.mark.asyncio
async def test_help_overlay_renders_keys():
    """HelpOverlay shows the key bindings."""
    from textual.app import App
    from textual.widgets import Static
    from finab.tui.widgets.help_overlay import HelpOverlay

    class _Host(App):
        def on_mount(self):
            self.push_screen(HelpOverlay())

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        modal = app.screen
        all_text = ""
        for s in modal.query(Static):
            all_text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        assert "c" in all_text and "category" in all_text.lower()
        assert "esc" in all_text.lower()


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
        assert isinstance(app.screen, HelpOverlay)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
