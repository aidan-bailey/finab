# CLI `--reset` flag — design

**Date:** 2026-06-26
**Status:** Approved (pending spec review)

## Problem

`finab` keeps two durable JSON state files in the working directory:

- `transactions.json` — the dedup map (`synced_transactions: {fw_uuid: ynab_import_id}`), owned by `TransactionsStore`.
- `config.json` — account mappings, merchant mappings, per-merchant
  categorization memory, `budget_id`, and `transfer_match_window_days`,
  owned by `ConfigStore` (and `config.py` for the top-level keys).

There is no supported way to start over from a clean slate. Today you'd have
to manually `rm` both files and remember their names. We want a first-class
`finab --reset` that does a **full reset** — deletes both files — behind a
confirmation prompt.

## Goals

- `finab --reset` deletes both `config.json` and `transactions.json`, then exits.
- Guard against accidents with a confirmation prompt (no backup).
- Be safe under test (must never touch the developer's real state files).
- Work without network access or valid API credentials.

## Non-goals

- Selective/partial reset (`--reset transactions` vs `--reset config`). Out of
  scope; full reset only.
- Backups of the deleted files.
- A `--force`/`--yes` flag to skip the prompt. May be added later; not now.

## Behavior

`finab --reset`:

1. Resolve the two target paths: `finab.store.CONFIG_FILE` and
   `finab.transactions.TRANSACTIONS_FILE`.
2. Filter to the ones that currently exist.
3. If none exist → print `Nothing to reset — no state files found.` and exit 0.
4. Otherwise print the files that will be deleted, then prompt:
   `Delete these files? [y/N] `.
   - Only `y` / `yes` (case-insensitive, stripped) proceeds.
   - Anything else → print `Reset cancelled.` and exit 0, deleting nothing.
5. On confirmation, `unlink()` each existing target, printing `Deleted <path>`
   per file, then `Reset complete.`
6. Return without launching the TUI. The user runs plain `finab` to start fresh.

Output is intentionally minimal — no setup hint after completion.

Plain `finab` (no `--reset`) behaves exactly as before.

## Argument parsing

`main()` currently takes no arguments and is wired as the console script
`finab = "finab.main:main"`. We introduce `argparse` (over a manual `sys.argv`
check) so we get `--help`, a clean `prog="finab"`, and an obvious home for
future flags. The entry point is unchanged; argparse reads `sys.argv` itself.

```python
def main():
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="finab",
        description="Sync FinWise transactions into YNAB.",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete config.json and transactions.json (full reset), then exit.",
    )
    args = parser.parse_args()
    if args.reset:
        run_reset()
        return
    # ... existing TUI launch unchanged ...
```

## Code shape

A standalone, testable helper in `main.py`:

```python
def run_reset(input_fn=input, output_fn=print) -> bool:
    """Full reset: delete config.json + transactions.json behind a confirm
    prompt. Returns True iff files were deleted. input_fn/output_fn are
    injected so tests can drive the prompt without real stdin/stdout."""
```

Key design points:

- **Paths resolved at call time** by reading the module attributes
  `finab.store.CONFIG_FILE` and `finab.transactions.TRANSACTIONS_FILE` — not
  captured at import. This mirrors how `ConfigStore`/`TransactionsStore`
  resolve their defaults lazily, so the `tests/conftest.py` sandbox (which
  monkey-patches those exact constants into a session tempdir) protects the
  reset path too. Capturing at import would let a test delete the real files.
- **No clients/stores constructed.** `run_reset` does pure filesystem + prompt
  work, so `--reset` needs no network and no valid `YNAB_ACCESS_TOKEN` /
  `FINWISE_API_KEY` — which is often the reason you're resetting.
- **`input_fn`/`output_fn` injection** lets tests confirm/cancel and assert on
  output deterministically.

Note: `finab.config.CONFIG_FILE` and `finab.store.CONFIG_FILE` are both
`Path("config.json")` (same file). We use `finab.store.CONFIG_FILE` as the
canonical handle for the config file.

## Testing

New tests (e.g. `tests/test_reset.py`), all within the conftest sandbox so no
real files are touched:

1. **Confirm deletes both** — create both sandbox files, call
   `run_reset(input_fn=lambda _: "y")`, assert both gone and returns True.
2. **Cancel keeps files** — both files exist, `input_fn=lambda _: "n"`, assert
   both remain and returns False.
3. **Case-insensitive yes** — `"YES"` / `" y "` proceed.
4. **Nothing to reset** — neither file exists, returns False, prints the
   "Nothing to reset" line, no error.
5. **Partial existence** — only one file exists; it is deleted and the run
   succeeds without erroring on the missing one.

Tests assert paths are read live from the patched module constants (the
conftest already re-points `finab.store.CONFIG_FILE` and
`finab.transactions.TRANSACTIONS_FILE`).

## Risks

- A user could fat-finger `y` and lose their mappings. Mitigated by the
  explicit file list + `[y/N]` default-no prompt. Accepted (no backup by
  design decision).
