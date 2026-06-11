"""Thesis-2 guard (ELS-227): control state never reads Linear.

The four control primitives — lease (``acquire_lock``), release, per-ws
cap (``count_active_locks``), cascade budget (``_count_recent_dispatches``)
— must resolve solely against Postgres. This test fails the build if a
primitive's SIGNATURE or any CALL SITE gains a Linear label/status
shaped input (the "control state sneaks into labels" regression the
headless pivot forbids).

Companion doc: documentation/internal/architecture/control-plane-vs-readmodel.md
"""

from __future__ import annotations

import ast
from pathlib import Path

DISPATCHER = (
    Path(__file__).resolve().parents[1] / "app" / "services" / "dispatcher.py"
)

_PRIMITIVES = {
    "acquire_lock",
    "release_lock",
    "count_active_locks",
    "_count_recent_dispatches",
    "sweep_expired_locks",
}

# Every parameter name a control primitive is ALLOWED to have/receive.
# Additions here are a deliberate architectural decision — if you are
# adding anything label/status/tracker-shaped, you are about to move
# control state into Linear: stop (thesis 2, headless-but-stateful).
_ALLOWED_PARAMS = {
    "session",
    "workspace_id",
    "key",
    "key_prefix",
    "ttl_seconds",
    "run_id",
    "ticket_ref",
    "action",
    "target_id",
    "window_s",
    "window",
    "now",
    "via",
    "reason",
    "audit",
    "self",
}

_FORBIDDEN_SUBSTRINGS = ("label", "status", "linear", "tracker_state", "issue_state")


def _parse() -> ast.Module:
    return ast.parse(DISPATCHER.read_text())


def test_primitive_signatures_have_no_linear_shaped_params() -> None:
    tree = _parse()
    seen = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and (
            node.name in _PRIMITIVES
        ):
            seen.add(node.name)
            params = [a.arg for a in node.args.args + node.args.kwonlyargs]
            for p in params:
                assert not any(s in p.lower() for s in _FORBIDDEN_SUBSTRINGS), (
                    f"{node.name}() grew a Linear-shaped parameter {p!r} — "
                    "control state must stay Postgres-only (thesis 2)."
                )
                assert p in _ALLOWED_PARAMS, (
                    f"{node.name}() grew an unexpected parameter {p!r}; if "
                    "it is genuinely Postgres-only, add it to _ALLOWED_PARAMS "
                    "deliberately."
                )
    # All four primitives must still exist — renaming them silently
    # would blind this guard.
    missing = {"acquire_lock", "release_lock", "count_active_locks",
               "_count_recent_dispatches"} - seen
    assert not missing, f"control primitives vanished from dispatcher.py: {missing}"


def test_call_sites_pass_no_linear_shaped_kwargs() -> None:
    tree = _parse()
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = None
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name not in _PRIMITIVES:
            continue
        checked += 1
        for kw in node.keywords:
            if kw.arg is None:
                continue
            assert not any(
                s in kw.arg.lower() for s in _FORBIDDEN_SUBSTRINGS
            ), (
                f"call to {name}() at line {node.lineno} passes "
                f"{kw.arg!r} — a Linear-shaped input into a control "
                "primitive (thesis-2 violation)."
            )
    assert checked >= 10, (
        f"expected >=10 control-primitive call sites in dispatcher.py, "
        f"found {checked} — the guard may have gone stale."
    )


def test_lock_table_has_no_linear_columns() -> None:
    """The lease table itself must stay Linear-free."""
    from backend.app.db.models.agent_dispatch import AgentDispatchLock

    cols = {c.name for c in AgentDispatchLock.__table__.columns}
    for col in cols:
        assert not any(s in col.lower() for s in _FORBIDDEN_SUBSTRINGS), (
            f"agent_dispatch_locks grew a Linear-shaped column {col!r}"
        )
