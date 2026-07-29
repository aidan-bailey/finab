"""Tests for WizardBanner — the setup-wizard step strip shown at the top
of FinabApp during first-run onboarding.
"""
import pytest


@pytest.mark.asyncio
async def test_wizard_banner_hidden_by_default():
    from textual.app import App
    from finab.tui.widgets.wizard_banner import WizardBanner

    class _Host(App):
        def compose(self):
            yield WizardBanner(id="wizard-banner")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#wizard-banner", WizardBanner)
        assert not banner.has_class("active")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert text.strip() == ""


@pytest.mark.asyncio
async def test_wizard_banner_show_renders_step():
    from textual.app import App
    from finab.tui.widgets.wizard_banner import WizardBanner

    class _Host(App):
        def compose(self):
            yield WizardBanner(id="wizard-banner")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#wizard-banner", WizardBanner)
        banner.show(2, 3, "map every account, then press n")
        await pilot.pause()
        assert banner.has_class("active")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert "2/3" in text
        assert "map every account" in text


@pytest.mark.asyncio
async def test_wizard_banner_hide_clears():
    from textual.app import App
    from finab.tui.widgets.wizard_banner import WizardBanner

    class _Host(App):
        def compose(self):
            yield WizardBanner(id="wizard-banner")

    app = _Host()
    async with app.run_test() as pilot:
        await pilot.pause()
        banner = app.query_one("#wizard-banner", WizardBanner)
        banner.show(1, 3, "pick a budget")
        await pilot.pause()
        banner.hide()
        await pilot.pause()
        assert not banner.has_class("active")
        text = str(getattr(banner, "content", "") or getattr(banner, "renderable", ""))
        assert text.strip() == ""
