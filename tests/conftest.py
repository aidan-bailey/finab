"""Test-wide safety net.

Some code paths (notably sync_transactions) instantiate stateful stores
with default *relative* paths (e.g. Path("transactions.json")) when no
explicit path is provided. If a test forgets to inject a temp store,
pytest — which runs with the repo root as cwd — would happily read,
mutate, and overwrite the developer's real config.json / transactions.json.

This conftest reroutes those module-level defaults to a session-scoped
temp directory so a forgotten injection turns into a sandbox write
instead of data destruction. Each test is responsible for the
specifics of *what* it writes; this just ensures it can't escape the
sandbox.
"""

import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True, scope="session")
def _sandbox_default_state_paths():
    sandbox = Path(tempfile.mkdtemp(prefix="finab-test-state-"))
    import finab.transactions as transactions_mod
    import finab.store as store_mod

    orig_txn_file = transactions_mod.TRANSACTIONS_FILE
    orig_cfg_file = store_mod.CONFIG_FILE
    orig_accts_file = store_mod.ACCOUNTS_FILE

    transactions_mod.TRANSACTIONS_FILE = sandbox / "transactions.json"
    store_mod.CONFIG_FILE = sandbox / "config.json"
    store_mod.ACCOUNTS_FILE = sandbox / "accounts.json"
    try:
        yield
    finally:
        transactions_mod.TRANSACTIONS_FILE = orig_txn_file
        store_mod.CONFIG_FILE = orig_cfg_file
        store_mod.ACCOUNTS_FILE = orig_accts_file
