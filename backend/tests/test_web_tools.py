"""Phase 2 web tools — resolver + provider-side web_search injection.

Two surfaces worth pinning without a DB / live HTTP:

1. :func:`resolve_firecrawl_key` — env-fallback when no integration
   row exists. The Integration-row path is exercised end-to-end in
   :mod:`test_integration_secrets` via the existing OAuth flows;
   here we only assert the env-only branch.

2. :func:`backend.app.services.agent.client._anthropic_tools` —
   injects Anthropic's native ``web_search_20250305`` server tool
   alongside every ToolSpec we pass. The role-prompt mentions
   ``web_search`` as a tool the LLM can call; the test pins that
   the wire shape actually includes it so the model isn't
   advertised a tool that doesn't exist.
"""

from __future__ import annotations


class _StubExecResult:
    """Minimal stand-in for the SQLAlchemy Result object so the
    resolver's ``.scalar_one_or_none()`` call returns synchronously
    without spinning up a real DB session."""

    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row


class _StubAsyncSession:
    """The resolver only awaits ``session.execute(...)`` and then
    calls ``.scalar_one_or_none()`` on the result. AsyncMock's
    attribute-magic makes both layers async by default, which is the
    wrong shape — so we hand-roll a tiny stub instead."""

    def __init__(self, row: object | None) -> None:
        self._row = row

    async def execute(self, _stmt: object) -> _StubExecResult:
        return _StubExecResult(self._row)


def test_resolve_firecrawl_key_returns_none_when_unset(monkeypatch) -> None:
    """No integration row, no env var → returns None. The tool layer
    surfaces this as ``firecrawl_unconfigured`` to the LLM rather
    than throwing."""
    from backend.app.services.firecrawl_resolver import resolve_firecrawl_key

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    async def run() -> object:
        return await resolve_firecrawl_key(
            _StubAsyncSession(None),
            "00000000-0000-0000-0000-000000000000",
        )

    import asyncio

    assert asyncio.run(run()) is None


def test_resolve_firecrawl_key_falls_back_to_env(monkeypatch) -> None:
    """Integration row missing but ``FIRECRAWL_API_KEY`` set in env →
    return the env value with ``source='env'``."""
    from backend.app.services.firecrawl_resolver import resolve_firecrawl_key

    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key-from-env")

    async def run() -> object:
        return await resolve_firecrawl_key(
            _StubAsyncSession(None),
            "00000000-0000-0000-0000-000000000000",
        )

    import asyncio

    resolved = asyncio.run(run())
    assert resolved is not None
    assert resolved.api_key == "test-key-from-env"
    assert resolved.source == "env"


def test_anthropic_tools_injects_server_web_search() -> None:
    """``_anthropic_tools`` always prepends Anthropic's native
    ``web_search_20250305`` server tool alongside the workspace's
    custom tools. The Navigator prompt promises ``web_search`` to
    the LLM; this test pins that the wire shape actually carries it."""
    from backend.app.services.agent.client import (
        ToolSpec,
        _anthropic_tools,
    )

    custom = ToolSpec(
        name="ticket_create",
        description="example",
        parameters={"type": "object"},
    )
    rendered = _anthropic_tools([custom])
    # First entry must be the server tool — the model sees it
    # advertised in the tools array.
    assert rendered[0] == {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": 5,
    }
    # Custom tools render unchanged behind the server tool.
    assert rendered[1]["name"] == "ticket_create"


def test_anthropic_tools_injects_web_search_even_with_no_custom_tools() -> None:
    """Edge case: empty ToolSpec list still emits the server tool so
    a model running with zero workspace tools (e.g. a brand-new
    workspace before activation) can still answer a web question."""
    from backend.app.services.agent.client import _anthropic_tools

    rendered = _anthropic_tools([])
    assert len(rendered) == 1
    assert rendered[0]["type"] == "web_search_20250305"
