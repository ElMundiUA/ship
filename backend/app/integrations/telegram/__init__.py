"""Telegram bot adapter — Telegram group chat ↔ Ship workspace bridge.

The bot worker runs as a backend long-poll process. It receives messages
from bound Telegram groups, calls the Navigator chat API on behalf of
the workspace, and streams the response back to Telegram by editing a
placeholder message in place.

Identity model is *shared workspace* — every message in a bound group
runs under the same service PAT. Per-user mapping can be layered on
later without changing the schema.
"""
