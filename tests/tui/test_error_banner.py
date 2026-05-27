import pytest


@pytest.mark.asyncio
async def test_error_banner_hidden_by_default():
    from finab.tui.app import FinabApp
    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#error-banner")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert text.strip() == ""


@pytest.mark.asyncio
async def test_error_banner_shows_loader_error():
    """If LoadedData.error is set, FinabApp surfaces it in the banner."""
    from finab.tui.app import FinabApp
    from finab.tui.data_loader import LoadedData

    app = FinabApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.loaded = LoadedData(error=RuntimeError("network down"))
        app._render_error_banner()
        await pilot.pause()
        banner = app.query_one("#error-banner")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert "network down" in text or "network down".lower() in text.lower()
