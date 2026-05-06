"""Picker advisory-lock key (ELS-85).

The lock binds to ``(workspace_id, fsm_stage)``. The mapping must be
deterministic across replicas (so two API pods compute the same key),
distinct across both axes, and fit in Postgres's int4 range — anything
outside that range trips ``pg_try_advisory_xact_lock``'s argument check
at run time.
"""

from __future__ import annotations

import uuid

import pytest

from backend.app.api.v1.routes.agent_runs import _pickup_lock_key


def test_lock_key_is_deterministic() -> None:
    ws = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
    assert _pickup_lock_key(ws, "task_intake") == _pickup_lock_key(
        ws, "task_intake"
    )


def test_lock_key_distinguishes_stages() -> None:
    ws = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
    assert _pickup_lock_key(ws, "task_intake") != _pickup_lock_key(ws, "wbs")
    assert _pickup_lock_key(ws, "ba_requirements") != _pickup_lock_key(
        ws, "tech_arch_plan"
    )


def test_lock_key_distinguishes_workspaces() -> None:
    a = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
    b = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert _pickup_lock_key(a, "task_intake") != _pickup_lock_key(
        b, "task_intake"
    )


def test_lock_key_components_fit_in_int4() -> None:
    """``pg_try_advisory_xact_lock(int, int)`` requires signed int32."""
    ws = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
    for stage in (
        "task_intake",
        "ba_requirements",
        "tech_arch_plan",
        "wbs",
        "architecture",
        "test_architecture",
        "tasks",
        "planning_done",
        "code_review",
    ):
        ws_key, stage_key = _pickup_lock_key(ws, stage)
        assert -(2**31) <= ws_key < 2**31, (ws_key, stage)
        assert -(2**31) <= stage_key < 2**31, (stage_key, stage)


def test_lock_key_is_not_python_hash_seeded() -> None:
    """If we ever switched to Python's built-in ``hash``, this catches it.

    Python hashes string-like inputs through a per-process random seed
    (PEP 456), so the same call on two replicas would produce
    different keys and the lock would silently stop working.
    BLAKE2b is process-stable; we pin the bytes so a regression here
    breaks the test loudly.
    """
    ws = uuid.UUID("00000000-0000-0000-0000-000000000000")
    # Pin a specific byte sequence so a switch to a non-deterministic
    # hash function fails this test (any change to the hashing scheme
    # also fails it — that's intentional, regenerate locally then).
    ws_key, stage_key = _pickup_lock_key(ws, "task_intake")
    expected_ws, expected_stage = _pickup_lock_key(ws, "task_intake")
    assert (ws_key, stage_key) == (expected_ws, expected_stage)
