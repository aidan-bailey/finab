# First-run setup wizard — design

**Date:** 2026-06-26
**Status:** Approved

## Problem

After `finab --reset`, `config.json` is gone, so `load_budget_id()` returns
`None`. At `app.py:128` the data fetch only kicks off when `budget_id` is
truthy, so the app boots into an empty Sync screen with no way to recover:
there is no budget picker, no first-run flow, and `save_budget_id` is imported
in `main.py` but never called (dead code). `SettingsScreen` is read-only
("Interactive budget switching … deferred to Plan 4").

We want first run (no `budget_id`) to launch a **guided setup wizard** that
walks the user through: pick budget → map accounts → map merchants → Sync.

## Goals

- On first run (no `budget_id`, clients present), enter a wizard.
- Step 1 Budget: required. Pick a YNAB budget; persist immediately.
- Step 2 Accounts: strict. Cannot advance until every FinWise account is
  mapped (in the store) or marked ignore.
- Step 3 Merchants: optional. Presented, but advance any time (Sync still
  catches unmapped merchants via `m`).
- Then land on Sync with data loaded.
- Existing users (with a `budget_id`) are never pulled into the wizard.

## Non-goals

- Re-entering the wizard on later launches once `budget_id` is saved (the
  wizard is a single-session onboarding; see Persistence).
- Auto-mapping accounts/merchants. Mapping uses the existing screen actions.
- Editing `.env` / credentials from inside the app.

## Approach

**Wizard controller over the existing screens** (chosen over dedicated wizard
screens or a modal chain). The app already exposes Accounts and Merchants as
freely-navigable screens; the wizard layers a temporary **navigation lock** and
a **step banner** over them and reuses their mapping logic unchanged.

## Components

### New

- `tui/widgets/budget_picker.py` — `BudgetPickerModal(ModalScreen[Optional[str]])`.
  Mirrors `YnabAccountPicker`: a title, a filter `Input`, and an `OptionList`
  of budgets (`name (id)`), each `Option` id = budget id. `Enter`/select
  dismisses with the chosen budget id; `escape` dismisses with `None`.
- `tui/widgets/wizard_banner.py` — `WizardBanner(Static)` like `ErrorBanner`:
  `show(step: int, total: int, text: str)` renders e.g.
  `Setup — Step 2/3: map every account, then press n`; `hide()` collapses it.

### Changed

- `tui/app.py`:
  - State: `_wizard_step: str | None` in `{None, "budget", "accounts",
    "merchants"}`.
  - `on_mount`: if clients present and `budget_id` is None → `_start_wizard()`;
    elif clients present and `budget_id` set → existing `_kickoff_load()`;
    else (no clients) → existing test-shell binding.
  - `_start_wizard()`: worker fetches `ynab_client.get_budgets()`, then pushes
    `BudgetPickerModal`. Empty list / fetch error → error banner + allow quit.
  - On budget chosen: `save_budget_id(id)`, set `self._budget_id`, refresh
    Settings, `_kickoff_load()`. `None` (escape) → `self.exit()` (nothing to do
    without a budget).
  - `_kickoff_load` end: if wizard active, call `_enter_accounts_step()` once
    data is bound (it already binds the Accounts/Merchants screens).
  - `_enter_accounts_step()` / `_enter_merchants_step()` / `_finish_wizard()`:
    set `_wizard_step`, switch the `ContentSwitcher`, update the banner.
    Finish clears `_wizard_step`, hides banner, unlocks nav, shows Sync.
  - `action_wizard_next` (bound `n`): on accounts step, advance only if
    `AccountsScreen.unmapped_count() == 0`, else bell + banner
    "N still unmapped"; on merchants step, finish.
  - Navigation lock: in `on_list_view_highlighted`, if `_wizard_step` is set,
    force `switcher.current` back to the step's screen and return.
  - `check_action`: `wizard_next` visible only while `_wizard_step` is set.
- `screens/accounts.py`, `screens/merchants.py`: extract `unmapped_count() -> int`
  from the existing inline "unmapped" computation in `refresh_rows` and reuse it
  there (no behavior change to the rows themselves).

## Data flow

1. Boot, `budget_id is None` → `_start_wizard()` → fetch budgets → modal.
2. Pick → save + set `self._budget_id` → `_kickoff_load()` (full `load_all`).
3. Load done → bind screens (existing) → `_enter_accounts_step()`.
4. User maps accounts (`l`/`a`/`i`); `n` gated on `unmapped_count() == 0`.
5. `_enter_merchants_step()`; `n` finishes regardless.
6. `_finish_wizard()` → Sync screen, nav unlocked.

## Persistence

`budget_id` is saved **immediately** on selection (intuitive). Consequence: a
mid-wizard quit means the next launch sees a saved `budget_id` and does NOT
re-enter the wizard; remaining accounts are mapped via the normal Accounts
screen. Accepted.

## Error handling

- `get_budgets()` raises or returns empty → error banner, no crash, quit allowed.
- `load_all` error mid-wizard → existing error banner; cannot advance to
  accounts without data.
- Budget picker escape → exit app.

## Testing (Pilot + fake clients, matching tests/tui/test_app.py)

1. `BudgetPickerModal` builds one option per budget; selecting dismisses with
   that budget id; escape dismisses with None.
2. Clients present + `budget_id None` → wizard starts (budget modal on screen).
3. Picking a budget calls `save_budget_id` (config.json gets the id) and
   advances to the accounts step (banner step 2, switcher on accounts).
4. Accounts gate: with an unmapped account, `n` stays on accounts; with zero
   unmapped, `n` advances to merchants.
5. Merchants step: `n` finishes → switcher on sync, banner hidden, nav unlocked.
6. Nav-lock: moving the sidebar during the wizard does not change the active
   screen.
7. `budget_id` already set → no wizard; normal `_kickoff_load` path.
8. `unmapped_count()` on both screens returns the count of unmapped FW
   accounts/merchants.

## Risks

- Textual timing in tests (workers/modals) — use `pilot.pause()` as existing
  TUI tests do.
- Nav-lock must not deadlock quit — `q` stays globally available.
