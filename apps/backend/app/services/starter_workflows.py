"""Starter workflow YAMLs for the Pipeline install flow.

RFC-0007 Phase 6 retires ``artifact_kind=workflow`` as a public catalog
surface: lanes-as-config + ``shipctl run`` + ``shipctl lanes install``
are the forward path, and external callers no longer need a browsable
``/workflows`` index. Internally, however, the Pipeline install flow
(``POST /v1/pipelines/install``, the GitHub-App auto-install webhook,
and the repo-secrets matrix) still commits a starter ``.yml`` into
``.github/workflows/`` on first adoption. That use-case is small and
well-scoped — four baked-in starters tied to lane recipes in
:mod:`backend.app.services.lane_recipes` — so we keep it alive as an
**internal** lookup, detached from the artifact catalog.

This module is the single source of truth for that detached lookup:

* Hard-coded metadata table for the four starter ids. Required fields
  stay tiny (``install_target``, optional ``required_secrets``) because
  any consumer that needs a richer surface should migrate to lanes.
* YAML bodies live at
  ``backend/app/resources/starter_workflows/<id>.yml``. The installer
  never mutates them; tests treat them as read-only fixtures.
* The API deliberately mirrors the relevant slice of the old
  :class:`backend.app.services.catalog.CatalogArtifact` (``.id``,
  ``.install_target``, ``.install_filename``, ``.required_secrets``,
  and an ``read_yaml()`` helper) so callers that used to ask
  ``catalog_service.get_workflow(...)`` can migrate with minimal
  churn.

No ``list_*`` accessor on purpose: we only want callers to reach a
starter by its pipeline id, never to enumerate the surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

_RESOURCES = Path(__file__).resolve().parents[1] / "resources" / "starter_workflows"


@dataclass(frozen=True, slots=True)
class StarterWorkflow:
    """Minimal read-only view of a starter workflow YAML.

    Compatible with the subset of :class:`CatalogArtifact` that the
    pipeline installer consumes; see module docstring for the
    migration context.
    """

    id: str
    install_target: str
    required_secrets: tuple[str, ...] = ()

    @property
    def install_filename(self) -> str:
        return self.install_target.rsplit("/", 1)[-1]

    def read_yaml(self) -> str:
        path = _RESOURCES / f"{self.id}.yml"
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover — tested via get()/read_starter_yaml()
            raise FileNotFoundError(
                f"Starter workflow YAML missing on disk: {path} ({exc})"
            ) from exc


# Order matches :func:`lane_recipes.list_lane_recipes` so the dashboard's install
# ordering stays stable. Keep this dict narrow — adding a row here is a
# product decision (one more baked-in starter workflow), not a catalog
# change; richer workflow configurations belong in lanes now.
_STARTERS: Final[dict[str, StarterWorkflow]] = {
    s.id: s
    for s in (
        StarterWorkflow(
            id="pr-and-ci-gate",
            install_target=".github/workflows/pr-and-ci-gate.yml",
        ),
        StarterWorkflow(
            id="scheduled-sdlc-lane",
            install_target=".github/workflows/scheduled-sdlc-lane.yml",
        ),
        StarterWorkflow(
            id="parallel-audit-lanes",
            install_target=".github/workflows/parallel-audit-lanes.yml",
        ),
        StarterWorkflow(
            id="pipeline-self-heal",
            install_target=".github/workflows/pipeline-self-heal.yml",
        ),
        # ELS-179 (W3) — ship-bootstrap.yml retired 2026-05-19; no live
        # install path. The wizard seed bundle only ships ship-agent-run.
        StarterWorkflow(
            id="ship-agent-run",
            install_target=".github/workflows/ship-agent-run.yml",
        ),
    )
}


def get(workflow_id: str) -> StarterWorkflow | None:
    """Return the starter for ``workflow_id`` or ``None``.

    ``None`` is not an error: some pipeline kinds (notably ``code_map``)
    have no YAML on purpose — the dispatcher resolves them in-process.
    """
    return _STARTERS.get(workflow_id)


def install_target(workflow_id: str) -> str | None:
    entry = get(workflow_id)
    return entry.install_target if entry else None


def install_filename(workflow_id: str) -> str | None:
    entry = get(workflow_id)
    return entry.install_filename if entry else None


def required_secrets(workflow_id: str) -> list[str]:
    entry = get(workflow_id)
    return list(entry.required_secrets) if entry else []


def read_yaml(workflow_id: str) -> str | None:
    """Return the raw YAML for ``workflow_id`` or ``None`` if unknown.

    Raises :class:`FileNotFoundError` when the id is known but the YAML
    resource is missing — that's a deployment bug (forgotten
    ``COPY backend/app/resources`` in the image) and should surface
    loudly, not degrade to a silent ``None``.
    """
    entry = get(workflow_id)
    return entry.read_yaml() if entry else None


__all__ = [
    "StarterWorkflow",
    "get",
    "install_target",
    "install_filename",
    "required_secrets",
    "read_yaml",
]
