"""ELS-120 safety net — code-changing stages require a PR URL.

The new finish protocol routes ``dev_implementation`` /
``qa_automation`` / ``workflow_self_heal`` through ``run.mjs``'s
sidecar flow, which splices ``PR: <github-url>`` into ``comment``
after ``gh pr create`` succeeds. A ``ready_next_step`` finish for
one of those stages without a PR URL means the agent bypassed the
sidecar — exactly the bug class ELS-120 closes. Reject with 422
``pr_url_required`` so the ticket doesn't advance to a stage that
has no PR to review against.

Non-code-changing stages (intake / BA / project-section /
``tasks`` / qa_manual / code_review) keep working without a PR
because their finish doesn't author one.
"""

from __future__ import annotations

import pytest


def _finish_payload(**overrides):
    """Default ``FinishIn`` shape — every test patches a few fields."""
    base = {
        "run_id": "test-run-id-001",
        "outcome": "ready_next_step",
        "fsm_stage": "dev_implementation",
        "stage_next": "qa_manual",
        "ticket_ref": "ELS-99",
        "comment": "Done. Did the thing. [Ship SDLC:role-developer]",
        "process": "development",
    }
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_dev_implementation_without_pr_url_returns_422(
    v1_client, seed_workspace
) -> None:
    """Agent bypassed the sidecar — direct ``/finish`` call without a
    PR URL in ``comment``. Server must refuse so the ticket stays at
    ``dev_implementation`` until a real PR exists."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(),
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["detail"]["code"] == "pr_url_required"
    assert body["detail"]["fsm_stage"] == "dev_implementation"


@pytest.mark.asyncio
async def test_dev_implementation_with_pr_url_in_comment_passes_gate(
    v1_client, seed_workspace
) -> None:
    """Sidecar-driven path: ``run.mjs`` appends ``PR: <url>`` to the
    comment after ``gh pr create``. The gate accepts and the call
    proceeds (downstream may fail for unrelated tracker reasons, but
    the gate itself must not 422)."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            comment=(
                "Done. Did the thing.\n\n"
                "PR: https://github.com/acme/repo/pull/42\n\n"
                "[Ship SDLC:role-developer]"
            ),
        ),
    )
    # The gate must not produce ``pr_url_required``. Downstream
    # tracker resolution may fail (no tracker bound on the test
    # workspace), but that's a separate path. The acceptance signal is
    # "not 422 with our specific code".
    if res.status_code == 422:
        assert res.json().get("detail", {}).get("code") != "pr_url_required"


@pytest.mark.parametrize(
    "stage",
    ["dev_implementation", "qa_automation", "workflow_self_heal"],
)
@pytest.mark.asyncio
async def test_all_code_changing_stages_gated(
    v1_client, seed_workspace, stage
) -> None:
    """Three stages live on the PR-authoring list — gate must fire
    for each one symmetrically."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(fsm_stage=stage, stage_next="next"),
    )
    assert res.status_code == 422, res.text
    assert res.json()["detail"]["code"] == "pr_url_required"
    assert res.json()["detail"]["fsm_stage"] == stage


@pytest.mark.parametrize(
    "stage",
    [
        "task_intake",
        "ba_requirements",
        "tech_arch_plan",
        "qa_arch_plan",
        "qa_manual",
        "code_review",
        "wbs",
        "architecture",
        "tasks",
    ],
)
@pytest.mark.asyncio
async def test_non_code_changing_stages_skip_gate(
    v1_client, seed_workspace, stage
) -> None:
    """Stages that don't author a PR (intake / BA / project-shaping /
    qa_manual / code_review) must not be gated by the PR-URL check
    — they finish with comment + description / project_sections /
    child_tickets and no branch."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(fsm_stage=stage, stage_next="next"),
    )
    # Downstream tracker resolution may fail (no tracker bound), but
    # the response must NOT carry ``pr_url_required``. Acceptance
    # signal is "PR gate did not trigger".
    if res.status_code == 422:
        assert res.json().get("detail", {}).get("code") != "pr_url_required"


@pytest.mark.asyncio
async def test_drain_finish_without_ticket_ref_skips_gate(
    v1_client, seed_workspace
) -> None:
    """Context-free routines (cron drain ticks with no eligible
    ticket) finish with ``ticket_ref=null``. The gate explicitly
    requires a ``ticket_ref`` before checking the PR URL — a
    null-ticket finish on dev_implementation is a drain, not a
    real code-change."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            ticket_ref=None,
            stage_next=None,
            comment="Routine drain tick — no eligible ticket. [Ship SDLC:role-developer]",
        ),
    )
    # No 422 from our gate. (Server may still reject for other
    # reasons, e.g. missing stage_next on ready_next_step; assert the
    # specific code if it's a 422.)
    if res.status_code == 422:
        assert res.json().get("detail", {}).get("code") != "pr_url_required"


@pytest.mark.asyncio
async def test_blocked_outcome_skips_pr_gate(
    v1_client, seed_workspace
) -> None:
    """``outcome=blocked`` on dev_implementation is the canonical
    "I can't ship" path — agent reports a runner-side failure
    (missing secret, push refused, etc). The gate is for
    ready_next_step only; blocked must always pass."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            outcome="blocked",
            stage_next=None,
            comment=(
                "Can't ship — `gh pr create` returned 403. "
                "[Ship SDLC:role-developer]"
            ),
        ),
    )
    if res.status_code == 422:
        assert res.json().get("detail", {}).get("code") != "pr_url_required"


@pytest.mark.asyncio
async def test_needs_clarification_skips_pr_gate(
    v1_client, seed_workspace
) -> None:
    """``needs_clarification`` is the agent waiting on a human;
    no PR is produced. Gate must not 422."""
    _, raw, workspace = seed_workspace
    res = await v1_client.post(
        f"/v1/workspaces/{workspace.id}/agent-runs/finish",
        headers={"Authorization": f"Bearer {raw}"},
        json=_finish_payload(
            outcome="needs_clarification",
            stage_next=None,
            comment="Should foo also bar? [Ship SDLC:role-developer]",
        ),
    )
    if res.status_code == 422:
        assert res.json().get("detail", {}).get("code") != "pr_url_required"
