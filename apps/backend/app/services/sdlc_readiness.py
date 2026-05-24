"""SDLC bootstrap readiness assessor (BS1).

Given a repo's project-type blueprint (:mod:`sdlc_blueprint`) and its
current state (the harvested file listing + frameworks + the repo's
GitHub Actions secret names), decide which of the blueprint's required
capabilities are already in place, which are gaps, which expected
secrets are present, and what the operator still has to do by hand.

The core (:func:`assess_readiness`) is a pure function over already-
gathered inputs so it's exhaustively unit-testable without a DB or the
network. :func:`build_readiness` orchestrates the I/O (load intel, pick
the blueprint, list files, list secrets) with both the gateway and the
secret lister injectable, mirroring ``repo_intel.harvest_repo_intel``.
"""

from __future__ import annotations

import fnmatch
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.inbox import InboxItem
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.integrations.gateway.code_host import CodeHostGateway, RepoRef
from backend.app.integrations.github.actions_secrets import list_repo_secrets
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost
from backend.app.services.repo_intel import get_current_intel
from backend.app.services.sdlc_blueprint import Blueprint, load_blueprint


# Lister hook so the endpoint can inject a fake in tests. Returns the
# repo's Actions secret names.
SecretLister = Callable[[], Awaitable[list[str]]]


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    capability: str
    required: bool
    satisfied: bool
    matched_by: str | None


@dataclass(frozen=True, slots=True)
class SecretStatus:
    name: str
    required: bool
    present: bool


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Pure assessment of one repo against its blueprint."""

    project_type: str
    delivery: str
    environments: tuple[str, ...]
    capabilities: tuple[CapabilityStatus, ...]
    gaps: tuple[str, ...]
    secrets: tuple[SecretStatus, ...]
    missing_required_secrets: tuple[str, ...]
    external_checklist: tuple[str, ...]
    ready: bool


@dataclass(frozen=True, slots=True)
class ReadinessResult:
    """Endpoint-facing wrapper that also covers the degraded cases
    (no intel harvested yet, no blueprint for the project type)."""

    repo_id: uuid.UUID
    intel_id: uuid.UUID | None
    project_type: str | None
    has_blueprint: bool
    detail: str | None
    report: ReadinessReport | None = field(default=None)


def _has_glob(probe: str) -> bool:
    return any(c in probe for c in "*?[")


def _probe_matches(
    probe: str,
    *,
    paths_lower: list[str],
    basenames_lower: list[str],
    dep_tokens: set[str],
    secret_tokens: set[str],
) -> bool:
    """Does ``probe`` (a file glob, dependency/tool token, or secret
    name) match the repo?

    Globs are matched with ``fnmatch`` against both the full path and
    the basename (against the glob's last segment, so ``**/*.test.*``
    catches a root-level ``a.test.ts`` too). Plain tokens match, in
    order: a configured GitHub Actions **secret name** (some capabilities
    are configured purely via secrets — e.g. desktop ``code_signing`` via
    ``CSC_LINK`` / ``APPLE_ID``), a harvested dependency/package-manager
    name, an exact basename, or a path substring.

    Note the harvested ``frameworks`` set is a *curated* application-
    framework list, so tooling dep tokens (``jest``, ``detox``,
    ``fastlane``) usually aren't in ``dep_tokens`` — they're caught
    instead by the sibling file-glob probe each capability ships
    (``**/*.test.*``, ``.detoxrc*``, ``**/Fastfile``). The substring
    fallback only ever over-credits a best-effort signal, never creates
    a gap.
    """
    p = probe.lower()
    if _has_glob(probe):
        last = p.rsplit("/", 1)[-1]
        if any(fnmatch.fnmatch(path, p) for path in paths_lower):
            return True
        return any(fnmatch.fnmatch(b, last) for b in basenames_lower)
    if p in secret_tokens:
        return True
    if p in dep_tokens:
        return True
    if p in basenames_lower:
        return True
    return any(p in path for path in paths_lower)


def assess_readiness(
    *,
    blueprint: Blueprint,
    paths: list[str],
    frameworks: list[str] | None = None,
    package_managers: list[str] | None = None,
    secret_names: list[str] | None = None,
) -> ReadinessReport:
    """Assess a repo against its blueprint. Pure — no I/O."""
    paths_lower = [p.lower() for p in paths]
    basenames_lower = [p.rsplit("/", 1)[-1].lower() for p in paths_lower]
    dep_tokens = {
        t.lower() for t in (*(frameworks or ()), *(package_managers or ()))
    }
    secret_set = set(secret_names or ())
    secret_tokens = {s.lower() for s in secret_set}

    cap_status: list[CapabilityStatus] = []
    for cap, probes in blueprint.capabilities.items():
        matched_by: str | None = None
        for probe in probes:
            if _probe_matches(
                probe,
                paths_lower=paths_lower,
                basenames_lower=basenames_lower,
                dep_tokens=dep_tokens,
                secret_tokens=secret_tokens,
            ):
                matched_by = probe
                break
        cap_status.append(
            CapabilityStatus(
                capability=cap,
                required=cap in blueprint.required,
                satisfied=matched_by is not None,
                matched_by=matched_by,
            )
        )

    gaps = tuple(
        c.capability for c in cap_status if c.required and not c.satisfied
    )

    secrets: list[SecretStatus] = []
    for name in blueprint.secrets_required:
        secrets.append(
            SecretStatus(name=name, required=True, present=name in secret_set)
        )
    for name in blueprint.secrets_optional:
        secrets.append(
            SecretStatus(name=name, required=False, present=name in secret_set)
        )
    missing_required_secrets = tuple(
        s.name for s in secrets if s.required and not s.present
    )

    external_checklist = tuple(
        item.text for item in blueprint.checklist if item.actor == "user"
    )

    ready = not gaps and not missing_required_secrets

    return ReadinessReport(
        project_type=blueprint.project_type,
        delivery=blueprint.delivery,
        environments=blueprint.environments,
        capabilities=tuple(cap_status),
        gaps=gaps,
        secrets=tuple(secrets),
        missing_required_secrets=missing_required_secrets,
        external_checklist=external_checklist,
        ready=ready,
    )


async def build_readiness(
    *,
    session: AsyncSession,
    repo: WorkspaceRepo,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    secret_lister: SecretLister | None = None,
) -> ReadinessResult:
    """Orchestrate a readiness assessment for one repo.

    Degrades gracefully: returns a result with ``has_blueprint=False``
    and a ``detail`` string when intel hasn't been harvested yet or no
    blueprint ships for the classified project type, rather than raising
    — the console card renders the detail.
    """
    intel = await get_current_intel(session, repo.id)
    if intel is None:
        return ReadinessResult(
            repo_id=repo.id,
            intel_id=None,
            project_type=None,
            has_blueprint=False,
            detail="Repo intel hasn't been harvested yet — run a harvest first.",
        )

    project_type = intel.project_type
    blueprint = load_blueprint(project_type)
    if blueprint is None:
        return ReadinessResult(
            repo_id=repo.id,
            intel_id=intel.id,
            project_type=project_type,
            has_blueprint=False,
            detail=(
                f"No bootstrap blueprint for project_type={project_type!r}. "
                "Bootstrap recommendations cover web / mobile / desktop."
            ),
        )

    s = settings or get_settings()
    install = (
        await session.get(GitHubInstallation, repo.installation_id)
        if repo.installation_id
        else None
    )

    owner, _, name = (repo.full_name or "").partition("/")
    gw = gateway or (
        GitHubCodeHost(install.installation_id, settings=s)
        if install is not None
        else None
    )
    # Can't inspect the tree → don't emit a confident all-gaps report
    # (every capability would look missing). Degrade with a detail the
    # console renders instead of the capability table.
    if gw is None or not (owner and name):
        return ReadinessResult(
            repo_id=repo.id,
            intel_id=intel.id,
            project_type=project_type,
            has_blueprint=True,
            detail=(
                "Repo isn't backed by a usable GitHub App installation, so "
                "Ship can't read the file tree to assess readiness."
            ),
        )

    ref = RepoRef(kind="github", owner=owner, repo=name)
    paths = await gw.list_files(ref, ref_sha=repo.default_branch or None)

    secret_names: list[str] = []
    if secret_lister is not None:
        secret_names = await secret_lister()
    elif install is not None:
        secret_names = await list_repo_secrets(repo, install, settings=s)

    report = assess_readiness(
        blueprint=blueprint,
        paths=paths,
        frameworks=list(intel.frameworks or []),
        package_managers=list(intel.package_managers or []),
        secret_names=secret_names,
    )
    return ReadinessResult(
        repo_id=repo.id,
        intel_id=intel.id,
        project_type=project_type,
        has_blueprint=True,
        detail=None,
        report=report,
    )


async def file_bootstrap_recommendation(
    *,
    session: AsyncSession,
    repo: WorkspaceRepo,
    settings: Settings | None = None,
    gateway: CodeHostGateway | None = None,
    secret_lister: SecretLister | None = None,
) -> InboxItem | None:
    """File an Inbox recommendation if the repo isn't bootstrap-ready.

    Called after an intel harvest. No-ops (returns ``None``) when there's
    no blueprint for the project type (backend/library/unknown), the tree
    couldn't be assessed, or the repo is already ready. Deduped on a
    per-repo ``intake_handle`` so repeat harvests don't pile up letters
    while one is still open.
    """
    result = await build_readiness(
        session=session,
        repo=repo,
        settings=settings,
        gateway=gateway,
        secret_lister=secret_lister,
    )
    report = result.report
    if not result.has_blueprint or report is None or report.ready:
        return None

    handle = f"bootstrap_readiness:{repo.id}"
    existing = (
        await session.execute(
            select(InboxItem.id).where(
                InboxItem.workspace_id == repo.workspace_id,
                InboxItem.intake_handle == handle,
                InboxItem.status == "new",
            )
        )
    ).first()
    if existing is not None:
        return None

    summary_parts = [
        f"This {report.project_type} repo is missing parts of its "
        "recommended SDLC setup."
    ]
    if report.gaps:
        summary_parts.append(f"Gaps: {', '.join(report.gaps)}.")
    if report.missing_required_secrets:
        summary_parts.append(
            f"Missing required secrets: "
            f"{', '.join(report.missing_required_secrets)}."
        )
    if report.external_checklist:
        summary_parts.append(
            f"{len(report.external_checklist)} setup step(s) need you."
        )

    item = InboxItem(
        workspace_id=repo.workspace_id,
        repo_id=repo.id,
        type="improvement",
        title=f"Set up {report.project_type} SDLC for {repo.full_name}"[:255],
        summary=" ".join(summary_parts),
        payload={
            "kind": "bootstrap_readiness",
            "project_type": report.project_type,
            "delivery": report.delivery,
            "environments": list(report.environments),
            "gaps": list(report.gaps),
            "missing_required_secrets": list(report.missing_required_secrets),
            "external_checklist": list(report.external_checklist),
            "intel_id": str(result.intel_id) if result.intel_id else None,
            # Informational for now: the "Generate bootstrap tickets"
            # action arrives in BS3 once the epic generator (executor)
            # exists. Surfacing a "generate" pill before then would
            # silently resolve the letter while doing nothing. Until
            # then the letter is an acknowledge-only recommendation —
            # the gaps + checklist below are the value.
            "action_items": [
                {
                    "id": "acknowledge",
                    "kind": "ack",
                    "label": "Got it",
                    "hint": (
                        "Re-run readiness from repo settings anytime. "
                        "Bootstrap ticket generation lands soon."
                    ),
                },
            ],
            "resolution_mode": "ack_only",
        },
        status="new",
        category="attention",
        priority=30,
        intake_handle=handle,
        intake_reason="bootstrap_readiness",
    )
    session.add(item)
    await session.flush()
    return item


__all__ = [
    "CapabilityStatus",
    "ReadinessReport",
    "ReadinessResult",
    "SecretStatus",
    "assess_readiness",
    "build_readiness",
    "file_bootstrap_recommendation",
]
