"""Tracker projection sync — probe + LLM resolve + open PR.

Single endpoint that takes a process + bound tracker integration and
opens a PR rewriting ``.ship/config.yml``'s ``process.tracker_mapping``
block so the seven canonical states are aligned with the team's actual
workflow. The full pipeline runs server-side:

1. Resolve the workspace's bound tracker (via the existing
   :func:`tracker_resolver.resolve_for_workspace`).
2. Probe the tracker for its workflow states + a few recent issue
   titles per state (LinearTracker.fetch_workflow_states +
   fetch_sample_titles_per_state).
3. Run :func:`tracker_projection_resolver.resolve_projection` —
   deterministic first, LLM second, validate + retry on bad output.
4. Patch the parsed YAML's ``process.tracker_mapping.<kind>`` slot
   with the resolved 7-entry map.
5. Open a single-file PR via the same ``commit_bundle_pr`` flow the
   regular config-propose endpoint uses.

The route deliberately does not expose a "review then commit" handshake
because the user's stated UX ask is "просто без агентов и рутин вызовем
напрямую ЛЛМ она все замапит и сразу закомитим" — review happens on
the GitHub PR, not in the console editor.

Currently Linear-only. Jira / GitHub / Notion adapter probes follow
the same shape; once they land, drop the kind-gate below and the
shared resolver will handle them transparently.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.v1.deps import AuthContext, get_current_auth
from backend.app.api.v1.routes.workspaces import (
    ROLES_ADMIN,
    _require_membership,
)
from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.tenancy import AuditLog
from backend.app.db.session import get_session
from backend.app.integrations.linear.tracker_adapter import LinearTracker
from backend.app.services.agent.client import pick_default_client
from backend.app.services.tracker_projection_resolver import (
    CANONICAL_STATES,
    ResolveResult,
    TrackerStateInfo,
    resolve_projection,
)
from backend.app.services.tracker_resolver import resolve_for_workspace


logger = logging.getLogger(__name__)


router = APIRouter(
    prefix="/workspaces/{workspace_id}/processes/{process_id}/tracker-sync",
    tags=["processes"],
)


_CONFIG_PATH = ".ship/config.yml"


class TrackerSyncIn(BaseModel):
    """Body for ``POST .../tracker-sync``."""

    repo_id: uuid.UUID = Field(
        description=(
            "The repo whose ``.ship/config.yml`` will be patched. The "
            "tracker projection is process-scoped on disk, so the route "
            "needs a concrete repo to know which file to rewrite — even "
            "though the tracker integration itself is workspace-scoped."
        ),
    )
    change_summary: str = Field(
        default="",
        max_length=1024,
        description=(
            "Optional human note rendered into the PR body so reviewers "
            "see what triggered the sync without opening the editor."
        ),
    )


class TrackerSyncOut(BaseModel):
    pr_url: str
    pr_number: int
    branch: str
    tracker_kind: str
    mapping: dict[str, str]
    llm_used: bool
    retries: int
    warnings: list[str] = Field(default_factory=list)
    deterministic_slots: list[str] = Field(default_factory=list)


@router.post("", response_model=TrackerSyncOut)
async def run_tracker_sync(
    workspace_id: uuid.UUID,
    process_id: str,
    payload: TrackerSyncIn,
    auth: AuthContext = Depends(get_current_auth),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> TrackerSyncOut:
    # Lazy imports — keep the rest of the v1 router free of the
    # GitHub gateway / catalog service modules unless the operator
    # actually triggers a sync.
    from backend.app.integrations.gateway.code_host import RepoRef
    from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
    from backend.app.integrations.github.workflows import (
        WorkflowDispatchError,
        commit_bundle_pr,
    )
    from backend.app.services import catalog as catalog_service

    await _require_membership(session, workspace_id, auth.user.id, ROLES_ADMIN)

    repo_row = (
        await session.execute(
            select(WorkspaceRepo).where(
                WorkspaceRepo.id == payload.repo_id,
                WorkspaceRepo.workspace_id == workspace_id,
            )
        )
    ).scalars().first()
    if repo_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Repo not found in this workspace.",
        )
    if repo_row.installation_id is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App isn't installed for this repo. "
                    "Reconnect it before syncing the tracker projection."
                ),
            },
        )
    install_row = await session.get(GitHubInstallation, repo_row.installation_id)
    if install_row is None or install_row.suspended_at is not None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "github_app_missing",
                "message": (
                    "Ship's GitHub App installation is missing or "
                    "suspended. Reinstall the Ship app."
                ),
            },
        )

    resolved_tracker = await resolve_for_workspace(
        session=session, settings=settings, workspace_id=workspace_id
    )
    if resolved_tracker is None:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "tracker_not_bound",
                "message": (
                    "No tracker is bound to this workspace. Connect "
                    "Linear (or another supported tracker) first."
                ),
            },
        )
    # Linear-only for now — Jira/GitHub/Notion adapter probes follow
    # the same shape and will plug into the same resolver once their
    # ``fetch_workflow_states`` methods land.
    if resolved_tracker.kind != "linear":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "tracker_kind_unsupported_for_sync",
                "kind": resolved_tracker.kind,
                "message": (
                    f"Tracker projection sync isn't wired for "
                    f"{resolved_tracker.kind!r} yet. Currently Linear only."
                ),
            },
        )
    gateway = resolved_tracker.gateway
    if not isinstance(gateway, LinearTracker):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal: tracker gateway shape mismatch.",
        )

    # ── probe the tracker ────────────────────────────────────────
    try:
        actual_states_raw = await gateway.fetch_workflow_states()
        sample_titles = await gateway.fetch_sample_titles_per_state(
            per_state_limit=3
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("tracker probe failed for ws=%s", workspace_id, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "tracker_probe_failed",
                "message": f"Couldn't read workflow states from tracker: {exc!s}",
            },
        ) from exc
    if not actual_states_raw:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "tracker_no_states",
                "message": (
                    "Tracker returned zero workflow states — check the "
                    "team has at least the default lifecycle configured."
                ),
            },
        )

    actual_states = [
        TrackerStateInfo(
            id=row["id"],
            name=row["name"],
            type=row.get("type") or "",
            samples=sample_titles.get(row["id"], []),
        )
        for row in actual_states_raw
    ]

    # ── resolve via deterministic + LLM ─────────────────────────
    try:
        client = pick_default_client(settings)
    except Exception:  # noqa: BLE001 — model env not configured
        client = None
    result: ResolveResult = await resolve_projection(
        tracker_kind=resolved_tracker.kind,
        actual_states=actual_states,
        client=client,
        model=settings.agent_model_fast,
    )
    new_mapping = {state: result.mapping[state] for state in CANONICAL_STATES}

    # ── read the live YAML so we can patch in place ─────────────
    owner, _, name = repo_row.full_name.partition("/")
    ref = RepoRef(kind="github", owner=owner, repo=name)
    code_host = GitHubCodeHost(install_row.installation_id, settings=settings)
    try:
        current_blob = await code_host.get_blob(
            ref, path=_CONFIG_PATH, ref_sha=repo_row.default_branch
        )
        if current_blob.encoding != "utf-8":
            raise HTTPException(
                status_code=status.HTTP_412_PRECONDITION_FAILED,
                detail={
                    "code": "config_encoding_unexpected",
                    "message": (
                        "``.ship/config.yml`` is not utf-8 text. "
                        "Tracker sync expects a plain-text YAML file."
                    ),
                },
            )
        raw_yaml = current_blob.content
    except FileNotFoundError:
        raw_yaml = ""
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "github_unreachable",
                "upstream_status": exc.response.status_code,
                "message": "GitHub rejected the config blob fetch.",
            },
        ) from exc

    try:
        parsed: dict[str, Any] = yaml.safe_load(raw_yaml) or {} if raw_yaml else {}
    except yaml.YAMLError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "config_yaml_unparseable",
                "message": (
                    "Current ``.ship/config.yml`` doesn't parse as YAML — "
                    "fix it manually before running tracker sync."
                ),
                "yaml_error": str(exc)[:512],
            },
        ) from exc

    process_block = parsed.get("process")
    if not isinstance(process_block, dict):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail={
                "code": "process_block_missing",
                "message": (
                    "Repo's ``.ship/config.yml`` has no ``process:`` "
                    "block. Run the seed bundle first so the canonical "
                    "process is committed before syncing tracker mapping."
                ),
            },
        )
    tracker_mapping_block = process_block.get("tracker_mapping")
    if not isinstance(tracker_mapping_block, dict):
        tracker_mapping_block = {}
    tracker_mapping_block[resolved_tracker.kind] = dict(new_mapping)
    process_block["tracker_mapping"] = tracker_mapping_block

    # Re-emit lanes from the parsed file so the round-trip preserves
    # whatever the operator already had. The lanes block is unchanged
    # by the tracker-sync flow but the emitter needs it as input.
    lanes_block = parsed.get("lanes")
    lanes_for_emit: dict[str, dict[str, Any]] = {}
    if isinstance(lanes_block, dict):
        for lane_id, trigger in lanes_block.items():
            if isinstance(trigger, dict):
                lanes_for_emit[str(lane_id)] = dict(trigger)
    new_yaml = catalog_service.emit_config_yaml(
        preset_id=parsed.get("preset") or repo_row.preset,
        repo_full_name=repo_row.full_name,
        lanes=lanes_for_emit,
        process=process_block,
    )

    pr_body = (
        "Ship: align ``process.tracker_mapping`` with this team's "
        f"{resolved_tracker.kind} workflow.\n\n"
        f"Resolver: deterministic={len(result.deterministic_slots)}/7, "
        f"LLM={'yes' if result.llm_used else 'no'}"
        + (f", retries={result.retries}" if result.retries else "")
        + ".\n\n"
        + (
            f"> {payload.change_summary}\n\n"
            if payload.change_summary
            else ""
        )
        + (
            "Warnings:\n" + "\n".join(f"- {w}" for w in result.warnings)
            if result.warnings
            else "No warnings."
        )
    )

    try:
        commit_result = await commit_bundle_pr(
            repo_row,
            install_row,
            files=[(_CONFIG_PATH, new_yaml)],
            title=f"Ship: align tracker_mapping with {resolved_tracker.kind} workflow",
            branch_label="tracker-sync",
            pr_body_header=pr_body,
            settings=settings,
            return_url=(
                f"{settings.console_url.rstrip('/')}/process"
                "?reason=pr_opened"
            ),
        )
    except WorkflowDispatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "code": "tracker_sync_pr_failed",
                "upstream_status": exc.status_code,
                "message": exc.message[:512],
            },
        ) from exc

    session.add(
        AuditLog(
            workspace_id=workspace_id,
            actor_user_id=auth.user.id,
            actor_token_id=auth.token.id if auth.token else None,
            action="process.tracker_sync",
            target_kind="workspace_repo",
            target_id=str(repo_row.id),
            payload={
                "process_id": process_id,
                "tracker_kind": resolved_tracker.kind,
                "pr_url": commit_result.pr_url,
                "pr_number": commit_result.pr_number,
                "branch": commit_result.branch,
                "llm_used": result.llm_used,
                "retries": result.retries,
                "deterministic_slots": result.deterministic_slots,
                "mapping": dict(new_mapping),
            },
        )
    )
    await session.commit()

    return TrackerSyncOut(
        pr_url=commit_result.pr_url,
        pr_number=commit_result.pr_number,
        branch=commit_result.branch,
        tracker_kind=resolved_tracker.kind,
        mapping=dict(new_mapping),
        llm_used=result.llm_used,
        retries=result.retries,
        warnings=list(result.warnings),
        deterministic_slots=list(result.deterministic_slots),
    )


__all__ = ["router"]
