"""Textual TUI for finab.

Subpackages:
  screens/  — full-screen views (Sync, Accounts, Merchants, Memory, Settings)
  widgets/  — reusable widgets and modal screens used across screens

This package may import from finab.engine, finab.store, finab.models,
finab.client, finab.ynab_client. The reverse is forbidden — engine and
data layers must remain Textual-free.
"""
