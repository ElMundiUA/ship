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


def test_lock_key_is_process_stable_pinned_bytes() -> None:
    """Pin literal byte values so a switch to a non-deterministic
    hash function (e.g. Python's built-in ``hash`` which is seeded
    per-process via PEP 456) fails the test loudly.

    The two replicas serving picker requests must compute **identical**
    keys for the same ``(workspace, fsm_stage)`` — otherwise the
    advisory lock doesn't actually serialise across replicas and
    the race-protection guarantee silently disappears. A previous
    iteration of this test only round-tripped the function with
    itself in the same process; that passes for both BLAKE2b AND
    Python's seeded hash, which is exactly the regression we want
    to catch.

    Expected values are BLAKE2b digests of the inputs; if you
    intentionally change the hashing scheme, regenerate them
    locally — that's the trade-off for catching the regression.
    """
    nil_ws = uuid.UUID("00000000-0000-0000-0000-000000000000")
    assert _pickup_lock_key(nil_ws, "task_intake") == (
        1_222_067_678,
        -845_439_783,
    )
    ship_on_ship = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
    assert _pickup_lock_key(ship_on_ship, "wbs") == (
        670_875_352,
        -1_080_360_883,
    )
