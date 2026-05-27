import pytest


@pytest.mark.asyncio
async def test_settings_screen_shows_budget_id_and_paths(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.settings import SettingsScreen
    from textual.widgets import ContentSwitcher, Static

    app = FinabApp(budget_id="my-budget-id-1234")
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-settings"
        await pilot.pause()
        screen = app.query_one(SettingsScreen)
        all_text = ""
        for s in screen.query(Static):
            all_text += str(getattr(s, "content", "") or getattr(s, "renderable", ""))
        assert "my-budget-id-1234" in all_text
        assert "config.json" in all_text
        assert "transactions.json" in all_text
