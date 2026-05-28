"""Phase 4 of the FSM event-driven rearchitecture — server-validated
``code_review → auto_merge`` transition.

The agent reports ``outcome=ready_next_step, stage_next=auto_merge``
at the code_review stage. Pre-Phase-4, the server trusted the claim,
moved the ticket to auto_merge, and dispatched the auto-merger; if
the reviewer agent had skipped the GitHub APPROVE (or CI was red),
the auto-merger hit GitHub's 405 "not mergeable" wall, audited
``github.auto_merge.failed``, and the ticket sat at auto_merge until
something else moved it. Wasted agent-tokens + one extra cycle.

Phase 4 gates the transition at the finish handler: the server calls
GitHub for the PR's review state + the head SHA's check_runs, and
only allows the move if BOTH "≥1 APPROVED review" AND "every
completed check_run is success/skipped/neutral" hold. Failure
downgrades ``outcome=ready_next_step`` to ``outcome=blocked`` so the
Phase 1 freeze flow runs (label + inbox letter); ``stage_next`` is
cleared so neither the transition call nor the cascade dispatch
fires.

These tests pin the helper's decision logic against synthetic GitHub
responses (httpx.MockTransport), one rejection reason per test. The
helper's audit + payload-mutation side effects in the finish handler
are covered by the live ELS-157/191/189 cascade verification noted
in the planning doc + the existing test_blocked_freeze_label.py
contract for the downstream blocked branch.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.api.v1.routes.agent_runs import (
    _extract_pr_url,
    _validate_code_review_to_auto_merge,
)


_OWNER = "acme"
_REPO = "ship"
_PR_NUMBER = 99
_PR_URL = f"https://github.com/{_OWNER}/{_REPO}/pull/{_PR_NUMBER}"
_HEAD_SHA = "0123456789abcdef0123456789abcdef01234567"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_settings():
    s = MagicMock()
    return s


def _mock_install_row():
    row = MagicMock()
    row.installation_id = 42
    row.suspended_at = None
    return row


def _gh_handler(*, reviews, pr_detail, check_runs):
    """Build an httpx.MockTransport handler returning the canned shapes."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/pulls/{_PR_NUMBER}/reviews"):
            if isinstance(reviews, int):  # status code override
                return httpx.Response(reviews, json={})
            return httpx.Response(200, json=reviews)
        if path.endswith(f"/pulls/{_PR_NUMBER}"):
            if isinstance(pr_detail, int):
                return httpx.Response(pr_detail, json={})
            return httpx.Response(200, json=pr_detail)
        if path.endswith(f"/commits/{_HEAD_SHA}/check-runs"):
            if isinstance(check_runs, int):
                return httpx.Response(check_runs, json={})
            return httpx.Response(200, json={"check_runs": check_runs})
        return httpx.Response(404, json={"message": f"unexpected path: {path}"})

    return handler


def _run_with_mocked_gh(
    *,
    reviews,
    pr_detail,
    check_runs,
    pr_url: str | None = _PR_URL,
):
    """Drive the validator with mocked install row + httpx transport."""
    transport = httpx.MockTransport(_gh_handler(
        reviews=reviews, pr_detail=pr_detail, check_runs=check_runs,
    ))

    class _MockedClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs.pop("timeout", None)
            super().__init__(*args, transport=transport, **kwargs)

    session = AsyncMock()
    scalar_one_or_none = MagicMock(return_value=_mock_install_row())
    exec_result = MagicMock(scalar_one_or_none=scalar_one_or_none)
    session.execute = AsyncMock(return_value=exec_result)

    with patch(
        "backend.app.integrations.github.app_auth.fetch_installation_token",
        new=AsyncMock(return_value="ghs_token"),
    ), patch.object(httpx, "AsyncClient", _MockedClient):
        import asyncio

        return asyncio.new_event_loop().run_until_complete(
            _validate_code_review_to_auto_merge(
                session,
                workspace_id=uuid.uuid4(),
                pr_url=pr_url,
                settings=_build_settings(),
            )
        )


# ---------------------------------------------------------------------------
# _extract_pr_url
# ---------------------------------------------------------------------------


def test_extract_pr_url_pulls_first_github_url() -> None:
    body = (
        "Done. PR: https://github.com/acme/ship/pull/42 — please review. "
        "[Ship SDLC:role-reviewer]"
    )
    assert _extract_pr_url(body) == "https://github.com/acme/ship/pull/42"


def test_extract_pr_url_returns_none_on_blank_input() -> None:
    assert _extract_pr_url("") is None
    assert _extract_pr_url(None) is None


def test_extract_pr_url_ignores_unrelated_github_urls() -> None:
    # The regex is anchored on /pull/<n> — non-PR GH URLs (issues,
    # blobs, raw files) must not match. If they did, the validator
    # downstream would try to fetch them as PRs and 4xx.
    body = "Saw a related issue: https://github.com/acme/ship/issues/41"
    assert _extract_pr_url(body) is None


# ---------------------------------------------------------------------------
# _validate_code_review_to_auto_merge — failure reasons
# ---------------------------------------------------------------------------


def test_no_pr_url_is_rejected() -> None:
    # The dev sidecar wraps gh pr create and embeds the URL. A finish
    # without it means the agent bypassed the sidecar — possibly
    # legitimately on an unusual stage, but for code_review → auto_merge
    # there's nothing to merge without a PR.
    ok, reason = _run_with_mocked_gh(
        reviews=[], pr_detail={}, check_runs=[], pr_url=None,
    )
    assert ok is False
    assert reason == "no_pr_url"


def test_invalid_pr_url_is_rejected() -> None:
    ok, reason = _run_with_mocked_gh(
        reviews=[], pr_detail={}, check_runs=[],
        pr_url="https://example.com/not-a-pr",
    )
    assert ok is False
    assert reason == "invalid_pr_url"


def test_no_approval_is_rejected() -> None:
    # PR exists, CI is fine — but the reviewer agent never actually
    # left an APPROVED review. The high-stakes case Phase 4 exists to
    # prevent: pre-Phase-4 the server would have advanced to
    # auto_merge and hit GH's 405 wall.
    ok, reason = _run_with_mocked_gh(
        reviews=[
            {"state": "COMMENTED"},
            {"state": "CHANGES_REQUESTED"},
        ],
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[],
    )
    assert ok is False
    assert reason == "no_approval"


def test_red_ci_is_rejected_even_with_approval() -> None:
    ok, reason = _run_with_mocked_gh(
        reviews=[{"state": "APPROVED"}],
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[
            {"name": "test", "status": "completed", "conclusion": "failure"},
        ],
    )
    assert ok is False
    assert reason == "ci_red"


def test_incomplete_ci_is_rejected_even_with_approval() -> None:
    # Run still in flight — don't advance until it lands. Phase 4
    # prefers a brief wait + retry over advancing then catching the
    # failure downstream.
    ok, reason = _run_with_mocked_gh(
        reviews=[{"state": "APPROVED"}],
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[
            {"name": "build", "status": "in_progress", "conclusion": None},
        ],
    )
    assert ok is False
    assert reason == "ci_incomplete"


def test_neutral_and_skipped_conclusions_pass() -> None:
    # GitHub uses "neutral" for advisory checks (linters that don't
    # block merge) and "skipped" for paths-filter early-outs. Neither
    # should block a Phase 4 transition.
    ok, reason = _run_with_mocked_gh(
        reviews=[{"state": "APPROVED"}],
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[
            {"name": "lint", "status": "completed", "conclusion": "neutral"},
            {"name": "skip", "status": "completed", "conclusion": "skipped"},
            {"name": "pass", "status": "completed", "conclusion": "success"},
        ],
    )
    assert ok is True
    assert reason == "ok"


def test_zero_check_runs_passes_with_approval() -> None:
    # Some workspaces have no CI configured. Branch protection
    # (required status checks) catches this downstream at merge time;
    # Phase 4 doesn't double-gate it.
    ok, reason = _run_with_mocked_gh(
        reviews=[{"state": "APPROVED"}],
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[],
    )
    assert ok is True
    assert reason == "ok"


def test_github_pr_api_error_is_rejected() -> None:
    # PR moved / deleted / permissions revoked between agent claim and
    # gate check. Fail closed so a 404 doesn't quietly advance the
    # ticket on a ghost PR.
    ok, reason = _run_with_mocked_gh(
        reviews=[{"state": "APPROVED"}],
        pr_detail=404,
        check_runs=[],
    )
    assert ok is False
    assert reason == "pr_api_404"


def test_github_reviews_api_error_is_rejected() -> None:
    ok, reason = _run_with_mocked_gh(
        reviews=500,
        pr_detail={"head": {"sha": _HEAD_SHA}},
        check_runs=[],
    )
    assert ok is False
    assert reason == "reviews_api_500"


def test_no_install_is_rejected() -> None:
    # Workspace has no GitHub App installation row. Without a token
    # we can't probe GitHub, so reject — Phase 1 freeze handles the
    # rest (operator sees the inbox letter and decides).
    session = AsyncMock()
    scalar_one_or_none = MagicMock(return_value=None)
    exec_result = MagicMock(scalar_one_or_none=scalar_one_or_none)
    session.execute = AsyncMock(return_value=exec_result)

    import asyncio

    ok, reason = asyncio.new_event_loop().run_until_complete(
        _validate_code_review_to_auto_merge(
            session,
            workspace_id=uuid.uuid4(),
            pr_url=_PR_URL,
            settings=_build_settings(),
        )
    )
    assert ok is False
    assert reason == "no_install"
