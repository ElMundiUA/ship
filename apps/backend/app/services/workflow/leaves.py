"""Leaf executors for the workflow runtime (W8.3).

The runtime never spawns anything itself — it calls the gate, then
hands the granted step to one of these executors. Two leaf species:

- **reasoning** — an in-process Navigator subagent turn
  (:meth:`ToolBox._run_subagent_loop`, role-prompted from the spec).
  Completes synchronously; its structured text is parsed into the
  step output.
- **coding** — a CI agent run: fire ``workflow_dispatch`` on
  ``ship-agent-run.yml`` exactly as the dispatcher does (reuse
  ``dispatch_workflow`` + ``WORKFLOW_FILE``), carrying the gate's
  correlation ``run_id``. Completion arrives later via the
  ``/agent-runs/finish`` webhook → :func:`complete_coding_step`
  (lean event-driven design: no in-process await; all state in
  ``agent_workflow_step_runs``).

Tests inject fake executors; production wiring uses these defaults.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings
from backend.app.db.models.workflow import AgentWorkflowStepRun
from backend.app.services.workflow.spec import StepSpec

logger = logging.getLogger(__name__)


# Executor signatures: (session, settings, workspace_id, step, inputs,
# run_id) → output dict for synchronous completion, or None when the
# leaf completes asynchronously (CI webhook path).
LeafExecutor = Callable[..., Awaitable[dict[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class LeafExecutors:
    run_reasoning: LeafExecutor
    run_coding: LeafExecutor
    # Deterministic context leaf (no LLM). Optional so test fakes that
    # predate the fetch kind keep constructing.
    run_fetch: LeafExecutor | None = None


# Long text inputs (a PR diff, a file body) render as their own
# fenced sections instead of being JSON-escaped into one line — and
# the old flat 16k cap silently destroyed exactly the context the
# fetch leaf exists to provide. Per-value cap keeps one huge diff
# from evicting the other inputs.
_LONG_VALUE_CHARS = 1000
_PER_VALUE_CAP = 60_000
_TOTAL_INPUT_CAP = 100_000


def _render_inputs(inputs: dict[str, Any]) -> str:
    short: dict[str, Any] = {}
    sections: list[str] = []
    for key, value in inputs.items():
        if isinstance(value, str) and len(value) > _LONG_VALUE_CHARS:
            body = value[:_PER_VALUE_CAP]
            suffix = (
                "\n… [truncated]" if len(value) > _PER_VALUE_CAP else ""
            )
            sections.append(f"## {key}\n\n```\n{body}{suffix}\n```")
        else:
            short[key] = value
    parts = []
    if short:
        parts.append(
            "Inputs:\n" + json.dumps(short, ensure_ascii=False, default=str)
        )
    parts.extend(sections)
    return "\n\n".join(parts)[:_TOTAL_INPUT_CAP]


_ROLE_FLAVOR = {
    "synthesize": (
        "You are a synthesizer. Merge the prior step outputs into one "
        "coherent result."
    ),
    "judge": (
        "You are a judge. Rank/score the prior step outputs and pick "
        "winners with reasons."
    ),
    "verify": (
        "You are a verifier. Adversarially re-check the claim(s) in "
        "your inputs; do not take them at face value."
    ),
}


async def run_reasoning_leaf(
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    step: StepSpec,
    inputs: dict[str, Any],
    run_id: str,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """In-process reasoning turn. The subagent loop sets
    ``_subagent_active`` for its tool calls, so a reasoning leaf can
    never call run_subagent/run_workflow (the W8.2 recursion guard)."""
    from backend.app.services.agent.tools import ToolBox

    toolbox = ToolBox(
        session,
        settings=settings,
        workspace_id=workspace_id,
        user_id=user_id,
    )
    flavor = _ROLE_FLAVOR.get(step.kind, "You are a focused specialist.")
    role = (step.agent.role if step.agent else None) or step.kind
    schema_clause = ""
    if step.output_schema:
        schema_clause = (
            "\n\nRespond with a single JSON object matching this JSON "
            f"Schema (no prose outside the JSON):\n"
            f"{json.dumps(step.output_schema, ensure_ascii=False)}"
        )
    system_prompt = (
        f"{flavor}\nRole: {role}. You are one bounded step "
        f"('{step.id}') of a deterministic Ship workflow — produce "
        "your result and stop; you cannot and must not spawn further "
        "agents." + schema_clause
    )
    prompt = step.agent.prompt if step.agent and step.agent.prompt else ""
    user_message = (
        (prompt + "\n\n" if prompt else "") + _render_inputs(inputs)
    )
    # Reasoning leaves run tool-less by default: their value is
    # synthesis over the inputs already collected by prior steps.
    result = await toolbox._run_subagent_loop(  # noqa: SLF001 — deliberate reuse (W8.1)
        system_prompt=system_prompt,
        user_message=user_message,
        tool_specs=[],
    )
    if result.get("error"):
        raise RuntimeError(
            f"reasoning leaf '{step.id}' failed: {result['error']}"
        )
    text = (result.get("text") or "").strip()
    if step.output_schema:
        try:
            # Tolerate fenced JSON.
            cleaned = text
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"reasoning leaf '{step.id}' did not return valid JSON: {exc}"
            ) from exc
    return {"text": text, "run_id": run_id}


async def run_coding_leaf(
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    step: StepSpec,
    inputs: dict[str, Any],
    run_id: str,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Fire the CI coding run, exactly as ticket dispatch does.

    Returns ``None`` — completion is asynchronous: the spawned agent
    reports via ``/agent-runs/finish`` with our ``run_id`` and
    :func:`complete_coding_step` closes the loop (releases the
    ``workflow:*`` lock, persists the output).
    """
    import httpx

    from backend.app.services.dispatcher import (
        WORKFLOW_FILE,
        _pick_dispatch_repo,
        dispatch_workflow,
    )

    target = await _pick_dispatch_repo(session, workspace_id=workspace_id)
    if target is None:
        raise RuntimeError(
            "no dispatch repo bound for this workspace — bind one in "
            "Settings → Repos before running coding leaves"
        )
    repo, install, _route = target
    routine_id = str(
        inputs.get("routine_id")
        or (step.agent.role if step.agent else None)
        or "workflow-leaf"
    )
    dispatch_inputs: dict[str, str] = {
        "routine_id": routine_id,
        "ticket_ref": str(inputs.get("ticket_ref") or ""),
        "ship_run_id": run_id,
    }
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        await dispatch_workflow(
            repo,
            install,
            WORKFLOW_FILE,
            inputs=dispatch_inputs,
            settings=settings,
            client=client,
        )
    return None


_GH_PR_RE = None  # compiled lazily below

_FETCH_MAX_BYTES = 400_000


async def run_fetch_leaf(
    session: AsyncSession,
    settings: Settings,
    workspace_id: uuid.UUID,
    step: StepSpec,
    inputs: dict[str, Any],
    run_id: str,
    *,
    user_id: uuid.UUID | None = None,
) -> dict[str, Any] | None:
    """Deterministic context leaf: GET ``inputs.url``, return the body.

    GitHub PR URLs (``github.com/<owner>/<repo>/pull/<n>``) are
    resolved to the API endpoint with ``Accept: …diff`` and, when the
    workspace has a GitHub App installation, authenticated with its
    installation token — so private-repo diffs work and public ones
    don't burn the anonymous rate limit. Output:
    ``{content, url, status, truncated}``.
    """
    import re as _re

    import httpx

    global _GH_PR_RE
    if _GH_PR_RE is None:
        _GH_PR_RE = _re.compile(
            r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
        )

    url = str(inputs.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise RuntimeError(f"fetch leaf '{step.id}': invalid url {url!r}")

    headers: dict[str, str] = {"User-Agent": "ship-workflow-fetch"}
    pr_match = _GH_PR_RE.match(url)
    if pr_match:
        owner, repo, number = pr_match.groups()
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}"
        headers["Accept"] = "application/vnd.github.diff"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        token = await _workspace_github_token(session, workspace_id, settings)
        if token:
            headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0), follow_redirects=True
    ) as client:
        res = await client.get(url, headers=headers)
    if res.status_code >= 400:
        raise RuntimeError(
            f"fetch leaf '{step.id}': GET {url} → {res.status_code}"
        )
    body = res.text or ""
    truncated = len(body) > _FETCH_MAX_BYTES
    return {
        "content": body[:_FETCH_MAX_BYTES],
        "url": url,
        "status": res.status_code,
        "truncated": truncated,
    }


async def _workspace_github_token(
    session: AsyncSession, workspace_id: uuid.UUID, settings: Settings
) -> str | None:
    """Best-effort installation token for the workspace's GitHub App
    install — ``None`` (anonymous fetch) on any miss."""
    try:
        from backend.app.db.models.integrations import GitHubInstallation
        from backend.app.integrations.github.app_auth import (
            fetch_installation_token,
        )

        install_id = (
            await session.execute(
                select(GitHubInstallation.installation_id)
                .where(GitHubInstallation.workspace_id == workspace_id)
                .order_by(GitHubInstallation.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if install_id is None:
            return None
        return await fetch_installation_token(install_id, settings=settings)
    except Exception:  # noqa: BLE001 — anonymous fetch is the fallback
        logger.warning(
            "fetch leaf: installation token unavailable ws=%s", workspace_id
        )
        return None


DEFAULT_EXECUTORS = LeafExecutors(
    run_reasoning=run_reasoning_leaf,
    run_coding=run_coding_leaf,
    run_fetch=run_fetch_leaf,
)


async def complete_coding_step(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
    run_id: str,
    success: bool,
    output: dict[str, Any] | None = None,
) -> bool:
    """Webhook-side completion: correlate a finished CI agent run back
    to its workflow step by ``run_id``, persist the outcome, release
    the ``workflow:*`` lock. Returns True when a step matched."""
    from backend.app.services.workflow.gate import release_step_lock

    row = (
        await session.execute(
            select(AgentWorkflowStepRun).where(
                AgentWorkflowStepRun.run_id == run_id,
                AgentWorkflowStepRun.status.in_(("dispatched", "running")),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.status = "completed" if success else "failed"
    row.output = output
    row.finished_at = datetime.now(timezone.utc)
    await release_step_lock(
        session,
        workspace_id=workspace_id,
        workflow_run_id=row.workflow_run_id,
        step_id=row.step_id,
    )
    await session.flush()
    return True
