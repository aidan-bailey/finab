import pytest


@pytest.mark.asyncio
async def test_account_link_picker_dismisses_with_transfer_payee_id(tmp_path):
    """Picking an account dismisses with its transfer_payee_id."""
    from textual.app import App
    from textual.widgets import OptionList
    from finab.store import ConfigStore
    from finab.tui.widgets.account_link_picker import AccountLinkPicker

    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase",
        fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpa"},
        ignore_transactions=False,
    )

    result_holder = {"value": "not-set"}

    class _Host(App):
        def on_mount(self):
            self.push_screen(
                AccountLinkPicker(store=store, title="Pick an account"),
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
    assert result_holder["value"] == "yn-tpa"
