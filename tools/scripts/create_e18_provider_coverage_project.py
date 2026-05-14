"""E18 — Production provider coverage: Trello tracker + Bitbucket git/CI.

One-shot scaffolder for the E18 epic in Ship-on-Ship Linear. Files the
provider-coverage gaps we've committed to in the closed-beta product
matrix (Linear / Jira / GitHub / Trello trackers + GitHub / GitLab /
Bitbucket / Azure DevOps code hosts).

Today we have:
  Trackers   — Linear ✓ · Jira ✓ · GitHub Issues ✓ · Trello ✗
  Code hosts — GitHub ✓ · GitLab ✓ · Azure DevOps ✓ · Bitbucket ✗
  CI         — GitHub Actions ✓ · GitLab CI ✓ · ADO Pipelines ✓ · Bitbucket Pipelines ✗

Two providers missing — Trello on the tracker side, Bitbucket on
both code-host and CI. This project parks the work so we can ship
the closed-beta promise without it blocking the local-dev sandbox
work (E19).

Idempotent — re-runs detect by name and skip what exists.

Usage:
    DATABASE_URL=... ENCRYPTION_KEY=... \\
      python tools/scripts/create_e18_provider_coverage_project.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.security.encryption import safe_decrypt
from backend.app.integrations.linear.tracker_adapter import LinearTracker


SHIP_ON_SHIP_WS = uuid.UUID("d591af28-225e-477e-8448-7a4b9b06fbfc")
ELS_TEAM_ID = "854ffe38-2ac7-404f-b482-7260ac707593"


PROJECT_NAME = "E18 — Provider coverage parity (Trello + Bitbucket)"

PROJECT_DESCRIPTION = (
    "Close the closed-beta provider matrix: add Trello to the "
    "tracker family and Bitbucket to the code-host + CI family. "
    "Existing gateway protocols (``TrackerGateway`` / ``CodeHostGateway`` "
    "/ ``CIGateway``) already abstract the contract — net new code is "
    "per-vendor; no orchestrator changes."
)

PROJECT_BODY = """\
We promised closed-beta operators that Ship works with the four most
common trackers (Linear / Jira / GitHub Issues / Trello) and four most
common code hosts (GitHub / GitLab / Bitbucket / Azure DevOps).
Current coverage:

| Surface | Linear | Jira | GitHub | Trello | GitLab | Bitbucket | ADO |
|---|---|---|---|---|---|---|---|
| Tracker | ✓ | ✓ | ✓ | **✗** | — | — | — |
| Code host | — | — | ✓ | — | ✓ | **✗** | ✓ |
| CI | — | — | ✓ | — | ✓ | **✗** | ✓ |

The work is straight implementation against existing protocols
(``apps/backend/app/integrations/gateway/{tracker,code_host,ci}.py``).
No orchestrator / picker / dispatcher changes — the FSM is
provider-agnostic.

## What changes for the operator

- Trello workspace appears in the Console "Connect tracker" picker;
  Trello OAuth1 dance lands a board → workspace binding the same way
  Linear / Jira / GitHub flows do.
- Bitbucket Cloud workspace appears in the "Connect code host" picker;
  PR / merge / comment flows work end-to-end; Bitbucket Pipelines
  spawns + reads status the same as GitHub Actions today.

## Why this is one project, not two

The gateway protocols are vendor-agnostic. Each new adapter is a leaf
implementation; once both Trello and Bitbucket land, the contract
test-suite (ticket below) pins them next to the four existing
adapters so future drift is caught at PR-time.

## Decisions to lock during planning

- Trello: OAuth1 with member API key + token (manual paste vs OAuth
  exchange) — keep parity with GH PAT flow if simpler.
- Bitbucket Cloud only (no Server / Data Center) for closed-beta.
- Pipelines: dispatched via ``pipelines/`` REST, read status via
  ``pipelines/{uuid}``. No Pipes / Pipelines-as-code generator.

## Out of scope

- Local-dev memory adapters (separate epic E19 — local sandbox).
- Notion as a tracker (de-prioritised; already partial adapter at
  ``integrations/notion/tracker_adapter.py``, no Console picker).
- Provider-quirk-debug accounts (handled by per-developer free-tier
  sandbox credentials, not a Ship deliverable).
"""


TICKETS: list[tuple[str, str]] = [
    (
        "E18-1: TrelloTracker adapter — TrackerGateway over Trello REST",
        """\
**Goal.** Implement the ``TrackerGateway`` protocol against Trello's
REST API, parity with ``LinearTracker`` / ``JiraTracker`` /
``GitHubIssuesTracker``.

**Scope.**

- New file ``apps/backend/app/integrations/trello/tracker_adapter.py``
  + ``__init__.py``.
- Map our domain ↔ Trello primitives:
  - workspace ↔ Trello board
  - project ↔ Trello list (or board labels — pick one and document)
  - ticket ↔ card
  - FSM state ↔ list position OR card label (pick the cleanest)
  - assignee ↔ member id
  - comments ↔ card actions (commentCard)
- Methods to implement (from ``gateway/tracker.py::TrackerGateway``):
  - ``next_task(state=...)`` — filter by stage label / list
  - ``transition(ticket_id, to_state)`` — move card between lists
    or swap stage label
  - ``create_ticket(title, body, project_id, labels)``
  - ``get_ticket(ticket_id)`` / ``list_tickets(...)``
  - ``add_comment(ticket_id, body)``
  - ``list_projects()`` / ``create_project(...)``
- Auth: API key + token (Trello's "Power-Up token" flow). Stored in
  ``native_integration_credentials`` like the others.
- Rate limit: Trello allows 100 req / 10s per key. Add the same
  retry-with-jitter wrapper ``LinearTracker`` uses.

**Acceptance.**
- Unit tests with mocked HTTP transport for every method.
- Smoke test against a real free-tier Trello board, gated by env
  (``E2E_TRELLO_API_KEY`` / ``E2E_TRELLO_TOKEN``).
- Contract test suite (ticket E18-5) parametrised over Linear / Jira
  / GitHub Issues / Trello passes.
""",
    ),
    (
        "E18-2: Trello integration row — Console picker + OAuth + workspace binding",
        """\
**Goal.** Operators can pick Trello from the "Connect tracker" wizard
the same way they pick Linear / Jira / GitHub today.

**Scope.**

- Migration: add ``trello`` to the
  ``native_integration_installations.provider`` enum (or its CHECK
  constraint, depending on how we encoded it).
- Console picker entry: ``apps/console/src/components/onboarding/...``
  list + per-provider step with Trello logo + auth instructions.
- Backend onboard route mirroring ``onboarding/tracker-install`` for
  Trello — accept API key + token, validate by listing boards, store
  encrypted credential row.
- Tracker resolver (``services/tracker_resolver.py``) wired so
  ``TrackerKind.trello`` returns a ``TrelloTracker`` instance.

**Acceptance.**
- Console "Connect tracker" step shows Trello.
- After connecting, the workspace's ``project.tracker_kind`` reads
  ``trello`` and ``GET /v1/workspaces/{ws}/tracker/next`` returns
  cards from the bound board.
- Audit log captures the install event.

**Depends on:** E18-1.
""",
    ),
    (
        "E18-3: Bitbucket code host adapter — CodeHostGateway over Bitbucket Cloud REST",
        """\
**Goal.** Implement ``CodeHostGateway`` against Bitbucket Cloud, parity
with ``GitHubCodeHostAdapter`` / ``GitLabCodeHostAdapter`` /
``AzureDevOpsCodeHostAdapter``.

**Scope.**

- New ``apps/backend/app/integrations/bitbucket/code_host_adapter.py``.
- Methods from ``gateway/code_host.py``:
  - ``ensure_repo`` (no-op on existing, create otherwise)
  - ``list_branches`` / ``create_branch`` / ``delete_branch``
  - ``get_blob`` / ``put_blob`` / ``commit``
  - ``open_pull_request(title, body, head, base, draft)``
  - ``list_pull_requests`` / ``get_pull_request``
  - ``merge_pull_request(pr_number, method, message)``
  - ``add_pr_comment(pr_number, body)``
- Bitbucket terminology: workspace → repository → pull request; the
  adapter normalises this onto our ``RepoRef`` / ``PullRequestRef``.
- Auth: Bitbucket OAuth2 consumer (preferred) or App Password
  fallback for closed-beta operators who don't want to register an
  OAuth app.

**Acceptance.**
- Unit tests with mocked HTTP for every method.
- Smoke test against a real Bitbucket sandbox repo gated by env
  (``E2E_BITBUCKET_USERNAME`` / ``E2E_BITBUCKET_APP_PASSWORD``).
- Contract test suite (E18-5) parametrised over GitHub / GitLab /
  ADO / Bitbucket passes.
""",
    ),
    (
        "E18-4: Bitbucket Pipelines CI adapter — CIGateway over /pipelines",
        """\
**Goal.** Implement ``CIGateway`` against Bitbucket Pipelines, parity
with ``GitHubActionsCIAdapter`` / ``GitLabCIAdapter`` /
``AzureDevOpsCIAdapter``.

**Scope.**

- New ``apps/backend/app/integrations/bitbucket/ci_adapter.py``.
- Methods from ``gateway/ci.py::CIGateway``:
  - ``dispatch(branch, workflow_ref, inputs)`` →
    ``POST /repositories/{ws}/{repo}/pipelines/`` with selector + variables
  - ``get_run(run_id)`` → ``GET /pipelines/{uuid}``
  - ``list_runs(branch=..., status=...)``
  - ``cancel_run(run_id)`` →
    ``POST /pipelines/{uuid}/stopPipeline``
- ``runs_status`` mapping: Bitbucket uses ``PENDING`` / ``IN_PROGRESS``
  / ``COMPLETED`` (with sub-state ``SUCCESSFUL`` / ``FAILED`` /
  ``STOPPED``). Normalise to our ``queued | running | success |
  failure | cancelled``.
- Auth: shares the OAuth/App-Password from E18-3.

**Acceptance.**
- Unit tests + smoke test against a real sandbox repo with a trivial
  ``bitbucket-pipelines.yml``.
- Contract test suite (E18-5) parametrised across the four CI
  adapters passes.

**Depends on:** E18-3 (shares auth + repo binding).
""",
    ),
    (
        "E18-5: Cross-provider contract test suite",
        """\
**Goal.** Pin the gateway protocols (``TrackerGateway`` /
``CodeHostGateway`` / ``CIGateway``) with a single parametrised
pytest suite so future drift between vendors gets caught at PR time
rather than in a customer integration call.

**Scope.**

- New ``apps/backend/tests/contract_tracker.py`` —
  ``@pytest.mark.parametrize("adapter_factory", [linear, jira, github,
  trello])`` with one shared body that exercises every protocol
  method. Each adapter factory yields a fake (mocked HTTP transport)
  pre-loaded with deterministic fixture data.
- Same for ``contract_code_host.py`` (github / gitlab / ado /
  bitbucket) and ``contract_ci.py`` (the four CIs).
- Fixtures encode our domain expectations (e.g. ``transition``
  doesn't lose comments, ``create_ticket`` returns a usable
  ``TicketRef`` with both id + url, ``merge_pull_request`` is
  idempotent on already-merged state).

**Acceptance.**
- ``pytest tests/contract_*.py -v`` runs all 12 adapter cases (4 ×
  3 protocols) green.
- New adapter additions only need to register a factory; everyone
  inherits the same expectations.
""",
    ),
]


def _dsn() -> tuple[str, dict]:
    raw = os.environ.get("DATABASE_URL") or os.environ.get("DB_URL")
    if not raw:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(2)
    if raw.startswith("postgresql://"):
        raw = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

    parts = urlsplit(raw)
    qs = dict(parse_qsl(parts.query))
    sslmode = qs.pop("sslmode", None)
    qs.pop("channel_binding", None)
    raw = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(qs), parts.fragment)
    )
    return raw, ({"ssl": True} if sslmode and sslmode != "disable" else {})


async def main() -> int:
    db_url, connect_args = _dsn()
    engine = create_async_engine(db_url, future=True, connect_args=connect_args)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as session:
        result = await session.execute(
            text(
                """
                SELECT nic.secret_ciphertext
                FROM native_integration_installations nii
                JOIN native_integration_credentials nic
                    ON nic.installation_id = nii.id
                WHERE nii.workspace_id = :ws
                  AND nii.provider = 'linear'
                  AND nic.kind = 'access_token'
                  AND nic.revoked_at IS NULL
                ORDER BY nic.updated_at DESC
                LIMIT 1
                """
            ),
            {"ws": SHIP_ON_SHIP_WS},
        )
        ct = result.scalar_one_or_none()
        if ct is None:
            print(
                "ERROR: no Linear access_token in native_integration_credentials "
                "for Ship-on-Ship workspace",
                file=sys.stderr,
            )
            return 3
        token = safe_decrypt(bytes(ct))
        if not token:
            print("ERROR: token decrypted to empty", file=sys.stderr)
            return 4

    tracker = LinearTracker(access_token=token, team_id=ELS_TEAM_ID)

    existing = await tracker.list_projects(limit=50, query=PROJECT_NAME)
    project = next(
        (p for p in existing if (p.get("name") or "").strip() == PROJECT_NAME),
        None,
    )
    if project:
        print(f"reuse project: {project['name']}  id={project['id']}")
        project_id = project["id"]
        project_url = project.get("url") or ""
    else:
        created = await tracker.create_project(
            name=PROJECT_NAME,
            description=PROJECT_DESCRIPTION,
            body=PROJECT_BODY,
        )
        project_id = created["id"]
        project_url = created["url"]
        print(f"created project: {created['name']}  id={project_id}")
        print(f"  url: {project_url}")

    existing_titles: set[str] = set()
    rows = await tracker.list_tickets(state="all", limit=100)
    for r in rows:
        existing_titles.add((r.get("title") or "").strip())

    created_count = 0
    skipped_count = 0
    for title, body in TICKETS:
        if title in existing_titles:
            print(f"  skip (exists): {title}")
            skipped_count += 1
            continue
        ticket = await tracker.create_ticket(
            title=title,
            body=body,
            project_id=project_id,
        )
        print(f"  + {ticket.display_id}  {title}")
        print(f"    {ticket.url}")
        created_count += 1

    print()
    print(f"done. project={project_url}  created={created_count}  skipped={skipped_count}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
