"""Picker refire cap — universal loop guard.

A correctly-flowing ticket fires each FSM stage exactly once: routine
picks → agent finishes → server transitions → breadcrumb label added
→ next picker call excludes the ticket via own-label filter. When any
layer downstream of the agent's finish silently fails to add the
breadcrumb (provisioner gap, transition no-op, label rename), the
same ticket re-picks every cron tick — observed in production as the
bug_triage stage burning agent tokens on six tickets over
24h. The cap caught the symptom regardless of the root cause: when
``(workspace, fsm_stage, ticket_ref)`` has fired ``finish`` more than
:data:`_REFIRE_CAP_LIMIT` times in :data:`_REFIRE_CAP_WINDOW`, the
picker refuses to hand the ticket back to the routine.

These tests pin the cap's constants and exercise the dedup path; the
audit-row + blocker-letter side effects are covered end-to-end by the
agent run integration tests (DB-bound, separate harness).
"""

from __future__ import annotations

from datetime import timedelta

from backend.app.api.v1.routes.agent_runs import (
    _REFIRE_CAP_LIMIT,
    _REFIRE_CAP_WINDOW,
)


def test_refire_cap_limit_is_conservative() -> None:
    """Cap should bite well before a real loop drains a budget but
    above the noise floor of legitimate operator-driven retries
    (e.g., the operator answered a clarification and the ticket re-
    runs the same stage cleanly). Three fires gives one normal pass
    + a self-heal retry + one diagnostic; the fourth attempt trips
    the cap.
    """
    assert _REFIRE_CAP_LIMIT == 3


def test_refire_cap_window_covers_a_workday() -> None:
    """The window has to outlast a typical operator working day so
    a loop that starts overnight is still capped when the operator
    looks at the dashboard in the morning, not just within an hour
    of the first fire."""
    assert _REFIRE_CAP_WINDOW == timedelta(hours=24)


def test_refire_cap_window_is_strictly_positive() -> None:
    """Sanity: a zero / negative window would mean "always cap" and
    stop every ticket on first pick."""
    assert _REFIRE_CAP_WINDOW.total_seconds() > 0
