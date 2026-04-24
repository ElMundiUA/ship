"""Lanes sync service — pull ``.ship/config.yml`` and upsert :class:`Lane` rows.

Entry points:

- :func:`sync_lanes_for_repo` — given a :class:`WorkspaceRepo` with a
  live GitHub App installation, fetch ``.ship/config.yml`` from the
  default branch, parse the v2 ``lanes:`` block, and upsert
  :class:`Lane` rows. Returns a :class:`SyncReport`.
- :func:`apply_workflow_run_completion` — called by the
  ``workflow_run.completed`` webhook to update a lane's
  ``last_run_at`` / ``last_run_status`` when the run's workflow file
  matches the generated ``ship-<lane_id>.yml`` wrapper.

Neither function raises for per-file parse errors — those land in
``report.errors`` so the caller (route handler or webhook) can log
them and still return a useful summary. Infrastructure faults
(install suspended, GitHub 5xx) bubble up as exceptions because
the caller's retry/409 logic is different for each.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.db.models.integrations import GitHubInstallation, WorkspaceRepo
from backend.app.db.models.lanes import Lane
from backend.app.integrations.gateway.code_host import RepoRef
from backend.app.integrations.github.code_host_adapter import GitHubCodeHost


logger = logging.getLogger(__name__)

CONFIG_PATH = ".ship/config.yml"

_VALID_LANE_KINDS = {"once", "event", "schedule"}
# ``lane_id`` gets embedded in ``ship-<lane_id>.yml`` and in CLI
# invocations. Mirror the CLI's slug rule so we never create a row
# the wrapper generator can't render.
_LANE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")


@dataclass
class SyncReport:
    """Summary of one :func:`sync_lanes_for_repo` pass."""

    repo_id: str
    added: int = 0
    updated: int = 0
    removed: int = 0
    unchanged: int = 0
    errors: list[str] = field(default_factory=list)
    sync_source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "errors": list(self.errors),
            "sync_source": self.sync_source,
        }


def _owner_repo(repo: WorkspaceRepo) -> tuple[str, str]:
    owner, _, name = (repo.full_name or "").partition("/")
    if not owner or not name:
        raise ValueError(f"invalid repo full_name: {repo.full_name!r}")
    return owner, name


def _parse_lane_entry(
    lane_id: str,
    raw: Any,
) -> tuple[str, dict[str, Any]] | None:
    """Normalise one ``lanes.<lane_id>`` YAML block.

    Returns ``(kind, flat_config_blob)`` or ``None`` when the entry
    is malformed (caller appends the reason to ``report.errors``).
    """
    if not isinstance(raw, dict):
        return None
    # Config v2 uses the trigger key itself as the kind discriminator:
    # ``once: install`` / ``event: pull_request`` / ``schedule: "0 9 * * *"``.
    # Pick the first recognised trigger; if two are set, prefer the
    # most specific (``once`` → ``event`` → ``schedule``) and flag
    # the others in ``config_blob`` so the Console can warn.
    kind: str | None = None
    for candidate in ("once", "event", "schedule"):
        if candidate in raw:
            kind = candidate
            break
    if kind is None:
        return None
    # Accept both the canonical ``patterns: [ids]`` (RFC-0008 C3.1) and
    # the single-pattern alias ``pattern: <id>``. Normalise to an
    # explicit list so the DB layer / downstream readers don't have to
    # branch; ``pattern`` (scalar) keeps the first entry so the
    # existing ``Lane.pattern`` column stays populated for repos that
    # haven't migrated to the list form yet.
    raw_patterns = raw.get("patterns")
    raw_pattern = raw.get("pattern")
    patterns_list: list[str] = []
    if isinstance(raw_patterns, list):
        for entry in raw_patterns:
            if isinstance(entry, str) and entry.strip():
                patterns_list.append(entry.strip())
    elif isinstance(raw_pattern, str) and raw_pattern.strip():
        patterns_list.append(raw_pattern.strip())
    primary_pattern = patterns_list[0] if patterns_list else None
    # RFC-0008 C3.2 — fan-out mode for multi-pattern lanes. Normalise
    # here so downstream consumers (API, scheduler, UI) always see one
    # of the canonical modes; validation is enforced by the writer
    # (``repos.propose_repo_config``) so the syncer trusts what's in
    # the YAML, but silently falls back to the default when the field
    # is absent or unrecognised.
    raw_fanout = raw.get("fanout")
    if isinstance(raw_fanout, str) and raw_fanout in {"matrix", "sequential", "concurrent"}:
        fanout = raw_fanout
    else:
        fanout = "matrix"
    flat = {
        "lane_id": lane_id,
        "kind": kind,
        "trigger": raw.get(kind),
        "pattern": primary_pattern,
        "patterns": patterns_list,
        "fanout": fanout,
        "cron": raw.get("schedule") if kind == "schedule" else None,
        "idempotency_key": raw.get("idempotency_key"),
        # Carry the whole thing so forward-compat fields land in
        # ``config_blob`` untouched.
        "raw": raw,
    }
    return kind, flat


async def sync_lanes_for_repo(
    *,
    session: AsyncSession,
    repo: WorkspaceRepo,
    install: GitHubInstallation,
    settings: Settings | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> SyncReport:
    """Fetch ``.ship/config.yml`` and upsert :class:`Lane` rows for ``repo``.

    Raises ``FileNotFoundError`` when the config file is absent (the
    route layer translates to a 409 with a hint). Raises
    ``httpx.HTTPStatusError`` for GitHub infrastructure errors.
    """
    s = settings or get_settings()
    owner, name = _owner_repo(repo)
    ref = RepoRef(kind="github", owner=owner, repo=name)

    gateway = GitHubCodeHost(
        install.installation_id, settings=s, client=http_client
    )
    blob = await gateway.get_blob(
        ref, path=CONFIG_PATH, ref_sha=repo.default_branch or None
    )
    if blob.encoding != "utf-8":
        raise ValueError(
            f"{CONFIG_PATH} in {repo.full_name} is not utf-8 text"
        )

    report = SyncReport(
        repo_id=str(repo.id),
        sync_source=f"{blob.sha}:{CONFIG_PATH}",
    )

    try:
        parsed = yaml.safe_load(blob.content) or {}
    except yaml.YAMLError as exc:
        report.errors.append(f"yaml parse: {exc}")
        return report

    if not isinstance(parsed, dict):
        report.errors.append("config root is not a mapping")
        return report

    # ``lanes:`` is the v2-only block. If missing we still want to
    # remove any previously-synced rows so the cache doesn't linger
    # after a downgrade/cleanup.
    lanes_block = parsed.get("lanes") or {}
    if not isinstance(lanes_block, dict):
        report.errors.append("`lanes:` is not a mapping")
        lanes_block = {}

    # P5-07 — promote any wizard-seeded synthetic rows whose
    # (lane_id, kind) still match the merged config IN PLACE before
    # the diff/update walk. This preserves ``last_run_at`` /
    # ``last_run_status`` across the synthetic→merged transition (a
    # ``once`` lane that fired between the seed and the first
    # scheduled tick keeps its history). Synthetic rows that the
    # merged config no longer references fall through to the
    # existing remove pass below.
    merged_specs: list[tuple[str, str]] = []
    for lane_id_pre, entry_pre in lanes_block.items():
        if not isinstance(lane_id_pre, str):
            continue
        parsed_pre = _parse_lane_entry(lane_id_pre, entry_pre)
        if parsed_pre is None:
            continue
        merged_specs.append((lane_id_pre, parsed_pre[0]))
    from backend.app.services.synthetic_lane_sync import (
        reconcile_synthetic_lanes,
    )

    await reconcile_synthetic_lanes(
        session=session,
        repo_id=repo.id,
        merged_lane_specs=merged_specs,
    )

    existing_rows = (
        await session.execute(
            select(Lane).where(Lane.repo_id == repo.id)
        )
    ).scalars().all()
    existing_by_id: dict[str, Lane] = {row.lane_id: row for row in existing_rows}

    now = datetime.now(timezone.utc)
    seen: set[str] = set()

    for lane_id, entry in lanes_block.items():
        if not isinstance(lane_id, str) or not _LANE_ID_RE.match(lane_id):
            report.errors.append(f"invalid lane_id: {lane_id!r}")
            continue
        parsed_entry = _parse_lane_entry(lane_id, entry)
        if parsed_entry is None:
            report.errors.append(
                f"lane {lane_id!r}: missing once/event/schedule trigger"
            )
            continue
        kind, flat = parsed_entry
        if kind not in _VALID_LANE_KINDS:  # pragma: no cover — guarded above
            report.errors.append(f"lane {lane_id!r}: unknown kind {kind!r}")
            continue

        seen.add(lane_id)
        row = existing_by_id.get(lane_id)

        cron_value: str | None = None
        if kind == "schedule":
            raw_cron = flat.get("trigger")
            if isinstance(raw_cron, str):
                cron_value = raw_cron[:128]

        pattern_value = flat.get("pattern")
        pattern_str = (
            str(pattern_value)[:255] if pattern_value is not None else None
        )
        idem_value = flat.get("idempotency_key")
        idem_str = (
            str(idem_value)[:255] if idem_value is not None else None
        )

        if row is None:
            row = Lane(
                workspace_id=repo.workspace_id,
                repo_id=repo.id,
                lane_id=lane_id,
                kind=kind,
                pattern=pattern_str,
                cron=cron_value,
                idempotency_key=idem_str,
                enabled=True,
                config_blob=flat,
                synced_at=now,
                sync_source=report.sync_source,
            )
            session.add(row)
            report.added += 1
            continue

        dirty = False
        if row.kind != kind:
            row.kind = kind
            dirty = True
        if row.pattern != pattern_str:
            row.pattern = pattern_str
            dirty = True
        if row.cron != cron_value:
            row.cron = cron_value
            dirty = True
        if row.idempotency_key != idem_str:
            row.idempotency_key = idem_str
            dirty = True
        if row.config_blob != flat:
            row.config_blob = flat
            dirty = True
        # Any synthetic row that survived the in-place promote pass
        # in ``reconcile_synthetic_lanes`` (e.g. operator changed the
        # lane's kind in the seed PR) is divergent from the
        # placeholder we wrote at install time but IS present in the
        # merged config — so the row's provenance is now genuinely
        # ``'merged'``. Flip it here so a third-party reader can
        # trust ``origin`` post-merge.
        if row.origin != "merged":
            row.origin = "merged"
            dirty = True

        row.synced_at = now
        row.sync_source = report.sync_source
        if dirty:
            row.updated_at = now
            report.updated += 1
        else:
            report.unchanged += 1

    # Remove rows for lanes no longer declared. We hard-delete rather
    # than soft-archive because the Lane row is a pure projection —
    # audit history lives on ``AuditLog`` (caller-owned) and on
    # ``PipelineRun`` (``lane_id`` FK is ``ON DELETE SET NULL``, so
    # historical runs survive the cleanup).
    for lane_id, row in existing_by_id.items():
        if lane_id in seen:
            continue
        await session.delete(row)
        report.removed += 1

    await session.flush()
    return report


# ---------------------------------------------------------------------------
# Webhook reconciliation — ``workflow_run.completed``
# ---------------------------------------------------------------------------

_LANE_WRAPPER_PATH_RE = re.compile(
    r"^\.github/workflows/ship-(?P<lane_id>[a-z][a-z0-9_-]{0,62})\.ya?ml$"
)


def extract_lane_id_from_path(path: str | None) -> str | None:
    """Return the ``lane_id`` encoded in a generated wrapper path, if any.

    ``shipctl lanes install`` renders ``.github/workflows/ship-<lane_id>.yml``,
    so the webhook can reverse-engineer which Lane a run belongs to by
    pattern-matching the ``path`` field of ``workflow_run`` events.
    Returns ``None`` for unrelated workflow files.
    """
    if not path:
        return None
    match = _LANE_WRAPPER_PATH_RE.match(path)
    if match is None:
        return None
    return match.group("lane_id")


def map_conclusion_to_status(conclusion: str | None) -> str:
    """GitHub ``conclusion`` → our ``last_run_status`` vocabulary."""
    if not conclusion:
        return "running"
    conclusion = conclusion.lower()
    if conclusion == "success":
        return "succeeded"
    if conclusion == "failure":
        return "failed"
    if conclusion in {"cancelled", "timed_out", "action_required", "neutral", "skipped", "stale"}:
        return conclusion
    return conclusion


async def apply_workflow_run_completion(
    *,
    session: AsyncSession,
    repo: WorkspaceRepo,
    workflow_path: str | None,
    conclusion: str | None,
    finished_at: datetime | None,
) -> Lane | None:
    """Update ``last_run_at`` / ``last_run_status`` for the matching lane.

    Returns the updated row (or ``None`` when the workflow file isn't
    a ship wrapper, or the lane was never synced). Safe to call for
    every ``workflow_run.completed`` event — the early-return on
    path mismatch makes this O(1) for the non-lane case.
    """
    lane_id = extract_lane_id_from_path(workflow_path)
    if lane_id is None:
        return None

    row = (
        await session.execute(
            select(Lane).where(
                Lane.repo_id == repo.id,
                Lane.lane_id == lane_id,
            )
        )
    ).scalars().first()
    if row is None:
        return None

    row.last_run_status = map_conclusion_to_status(conclusion)
    row.last_run_at = finished_at or datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    return row


__all__ = [
    "CONFIG_PATH",
    "SyncReport",
    "apply_workflow_run_completion",
    "extract_lane_id_from_path",
    "map_conclusion_to_status",
    "sync_lanes_for_repo",
]
