"""Headless engine modules — no Textual imports, no interactive I/O.

`sync.py` owns phase 3 (transaction processing) as a state machine.
`accounts.py` and `merchants.py` hold pure helpers for phases 1 and 2.

Import direction: `tui/*` may import from here; this package may NOT
import anything from `finab.tui` or `textual`.
"""
