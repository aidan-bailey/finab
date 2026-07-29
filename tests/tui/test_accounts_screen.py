import pytest


def _seed_store(tmp_path):
    from finab.store import ConfigStore
    store = ConfigStore(tmp_path / "config.json")
    store.add_account(
        alias="Chase Checking",
        fw_record={"id": "fw-a", "name": "Chase", "type": "checking", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-a", "name": "Chase", "type": "checking", "balance": 0, "transfer_payee_id": "yn-tpa"},
        ignore_transactions=False,
    )
    store.add_account(
        alias="Crypto Wallet",
        fw_record={"id": "fw-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "currency_code": "USD"},
        ynab_record={"id": "yn-b", "name": "Crypto", "type": "otherAsset", "balance": 0, "transfer_payee_id": "yn-tpb"},
        ignore_transactions=True,
    )
    return store


@pytest.mark.asyncio
async def test_accounts_screen_lists_accounts(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        await pilot.pause()
        assert ac_screen.row_count() == 2


@pytest.mark.asyncio
async def test_accounts_screen_toggle_ignore(tmp_path):
    """Calling action_toggle_ignore on the screen flips the store entry."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac_screen = app.query_one(AccountsScreen)
        ac_screen.refresh_rows()
        ac_screen.set_cursor(0)
        await pilot.pause()
        ac_screen.action_toggle_ignore()
        await pilot.pause()
        acc = store.account_by_finwise_id("fw-a")
        assert acc["ignore_transactions"] is True


class _FakeFwAccount:
    """Stub matching the FinWise-side Account model fields the screen reads."""
    def __init__(self, finwise_id, name, type="checking"):
        self.finwise_id = finwise_id
        self.name = name
        self.type = type
        self.balance = 0
        self.currency_code = "USD"


@pytest.mark.asyncio
async def test_accounts_unmapped_count(tmp_path):
    """unmapped_count() returns the number of FW accounts not in the store."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)  # store has fw-a and fw-b
    fw_accounts = [
        _FakeFwAccount("fw-a", "Chase"),     # mapped
        _FakeFwAccount("fw-c", "BoA Card"),  # unmapped
        _FakeFwAccount("fw-d", "Discover"),  # unmapped
    ]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(store=store, fw_accounts=fw_accounts, ynab_accounts=[], ynab_client=None, budget_id=None)
        await pilot.pause()
        assert ac.unmapped_count() == 2


@pytest.mark.asyncio
async def test_accounts_unmapped_count_zero_when_all_mapped(tmp_path):
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)
    fw_accounts = [_FakeFwAccount("fw-a", "Chase"), _FakeFwAccount("fw-b", "Crypto")]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(store=store, fw_accounts=fw_accounts, ynab_accounts=[], ynab_client=None, budget_id=None)
        await pilot.pause()
        assert ac.unmapped_count() == 0


@pytest.mark.asyncio
async def test_accounts_screen_shows_unmapped_fw_accounts(tmp_path):
    """When `fw_accounts` contains accounts not in the store, they
    render as unmapped rows with the `!` glyph."""
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from textual.widgets import ContentSwitcher

    store = _seed_store(tmp_path)  # has fw-a (Chase) and fw-b (Crypto)
    fw_accounts = [
        _FakeFwAccount("fw-a", "Chase"),         # already in store
        _FakeFwAccount("fw-c", "BoA Card"),       # NEW — unmapped
    ]
    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(store=store, fw_accounts=fw_accounts, ynab_accounts=[], ynab_client=None, budget_id=None)
        await pilot.pause()
        # 2 mapped + 1 unmapped = 3 rows.
        assert ac.row_count() == 3
        # The unmapped row uses '!' glyph.
        assert ac.has_unmapped_for("fw-c")


class _FakeYnabAccount2:
    """Stub for the YNAB account-side data, including the .data.account
    response shape that YNABClient.create_account returns."""
    def __init__(self, id, name, type="checking"):
        self.id = id
        self.ynab_id = id
        self.name = name
        self.type = type
        self.balance = 0
        self.deleted = False
        self.closed = False
        self.transfer_payee_id = f"tp-{id}"


class _StubYnabClient:
    """Just enough of YNABClient for the mapping flow tests."""
    def __init__(self):
        self.created = []

    def create_account(self, budget_id, account):
        new = _FakeYnabAccount2(
            id=f"yn-new-{len(self.created)}",
            name=account.name,
            type=account.type,
        )
        self.created.append(new)

        class _Resp:
            class data:
                pass

        resp = _Resp()
        resp.data.account = new
        return resp


@pytest.mark.asyncio
async def test_link_unmapped_account_to_new_ynab(tmp_path):
    """Type alias that doesn't match → confirm create → YNAB account created + linked."""
    from finab.store import ConfigStore
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from finab.tui.widgets.yes_no_modal import YesNoModal
    from textual.widgets import ContentSwitcher

    store = ConfigStore(tmp_path / "config.json")
    fw_accounts = [_FakeFwAccount("fw-new", "BoA Card", type="creditCard")]
    ynab_client = _StubYnabClient()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(
            store=store,
            fw_accounts=fw_accounts,
            ynab_accounts=[],
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        ac.set_cursor(0)
        await pilot.pause()
        ac.action_link()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        app.screen.dismiss("BoA Credit")
        await pilot.pause()
        assert isinstance(app.screen, YesNoModal)
        await pilot.press("y")
        await pilot.pause()
        # YNAB account creation happened.
        assert len(ynab_client.created) == 1
        # Store now has the new account.
        acc = store.account_by_finwise_id("fw-new")
        assert acc is not None
        assert acc["alias"] == "BoA Credit"


@pytest.mark.asyncio
async def test_link_unmapped_account_to_existing_ynab(tmp_path):
    """Alias matches an existing YNAB account → link without creating new."""
    from finab.store import ConfigStore
    from finab.tui.app import FinabApp
    from finab.tui.screens.accounts import AccountsScreen
    from finab.tui.widgets.alias_input import AliasInputModal
    from textual.widgets import ContentSwitcher

    store = ConfigStore(tmp_path / "config.json")
    fw_accounts = [_FakeFwAccount("fw-new", "BoA Card")]
    ynab_accounts = [_FakeYnabAccount2("yn-boa", "BoA Credit")]
    ynab_client = _StubYnabClient()

    app = FinabApp(store=store)
    async with app.run_test() as pilot:
        await pilot.pause()
        switcher = app.query_one("#content-switcher", ContentSwitcher)
        switcher.current = "screen-accounts"
        await pilot.pause()
        ac = app.query_one(AccountsScreen)
        ac.bind_data(
            store=store,
            fw_accounts=fw_accounts,
            ynab_accounts=ynab_accounts,
            ynab_client=ynab_client,
            budget_id="bid",
        )
        await pilot.pause()
        ac.set_cursor(0)
        ac.action_link()
        await pilot.pause()
        assert isinstance(app.screen, AliasInputModal)
        app.screen.dismiss("BoA Credit")
        await pilot.pause()
        # No YesNoModal — match found → linked directly.
        assert ynab_client.created == []
        acc = store.account_by_finwise_id("fw-new")
        assert acc is not None
        assert acc["ynab"]["id"] == "yn-boa"
