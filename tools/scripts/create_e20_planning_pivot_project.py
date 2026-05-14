"""E20 — Mid-thread planning entry/exit (Navigator pivot).

Today the only way into ``intent='shape_project'`` is the dashboard's
"+ New project" CTA which archives the current chat and opens a
fresh thread. Two pain-points operators have hit:

1. Pivot mid-conversation: in the middle of a normal chat the user
   says "actually, let's shape a new project around this idea" —
   today they have to leave the thread (losing context) and click
   the CTA from the dashboard. We want Navigator to detect the
   intent + offer an inline switch that flips ``intent=shape_project``
   in-place without archiving the thread.
2. Exit-without-create: in drafting mode the user changes their mind
   ("nah, forget it, tell me about CSV export instead"). Today the
   only escape hatch is the ``create_project`` tool (which would
   actually create the project) — there's no clean "exit drafting
   mode" path. We want Navigator to detect intent flip + offer
   "Exit drafting" inline.

Tests for the existing planning surface (Variant A — open new thread
with intent, create_project succeeds, intent resets, fact tagged)
will land regardless of this epic. E20 covers the *new* UX that
needs implementation + dedicated tests.

Idempotent — re-runs detect by name and skip what exists.
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


PROJECT_NAME = "E20 — Mid-thread planning pivot (in-thread enter/exit)"

PROJECT_DESCRIPTION = (
    "Let users pivot into and out of ``intent='shape_project'`` "
    "without leaving the current thread. Replaces today's "
    "thread-archiving CTA with an in-thread mode flip + adds a "
    "clean exit path that doesn't require ``create_project``."
)

PROJECT_BODY = """\
Today's planning UX has two friction points the closed-beta operators
keep tripping over:

- **Enter is destructive.** "+ New project" archives the current
  chat and opens a fresh thread with ``intent='shape_project'``.
  Everything the user just typed lives on in the archive, but the
  agent loses context — it can't reach back across the archive
  boundary cheaply. People walk away mid-shape.
- **Exit requires create.** Once ``intent='shape_project'`` is set,
  the only path back is the ``create_project`` tool. If the user
  changes their mind ("ah, forget it, what was that thing you
  were saying about CSV export?") there's no escape hatch — the
  drafting prompt keeps trying to shape a project from every
  subsequent message.

The Navigator memory work (E17) covered the *retrieval* angle; this
epic covers the *intent* state machine itself.

## Proposed UX

**Enter mid-thread.** When the topic-shift classifier (or a
heuristic from the agent's response) detects the user is shaping
a project, Navigator emits an inline CTA:

> "Looks like you're shaping a new project. Switch to drafting
>  mode?  [Switch]  [No thanks]"

Click → server-side: ``thread.intent = "shape_project"``,
``last_user_activity_at`` ticks, no archive. The thread stays
single — only the agent's mode changes. The drafting prompt is
appended to the system context starting next turn.

**Exit mid-thread.** When the user's reply doesn't fit the
drafting model (topic-shift detected away from project-shape),
Navigator emits the inverse CTA:

> "Want to stop drafting and chat about something else? You can
>  always pick this thread back up later.  [Exit drafting]  [Keep shaping]"

Click → ``thread.intent = None``. Any partial draft is preserved
in the thread; the user can come back via "+ Resume drafting"
from the thread sidebar.

## Architecture sketch

- New REST endpoint: ``POST /v1/workspaces/{ws}/chat/active/intent``
  with body ``{intent: "shape_project" | null}``. Strict — must come
  from the active thread, returns 409 if the thread is archived.
- SSE event addition: ``intent_change`` carrying the new intent so
  the UI rerenders the system-bar tag ("Drafting" badge) without
  a refresh.
- Agent prompts pick up the new mode on the *next* user turn —
  no half-mode mid-stream.
- mem0 facts captured during drafting carry
  ``intent_at_capture=shape_project`` (ELS-129); facts captured
  after exit-without-create get ``intent_at_capture=None`` — no
  ``project_native_id`` tag yet because the project hasn't been
  born.

## Decisions to lock during planning

- Detector: rely on the topic-shift classifier or build a separate
  "drafting-intent" classifier? Recommendation: reuse the topic-shift
  output + a small post-classifier that maps "shift towards new
  project shape" to the enter-CTA.
- Auto-flip vs. confirm-flip: never auto-flip silently. Always render
  the inline CTA — operator clicks. Drafting mode changes a lot of
  downstream behaviour; surprise mode-changes erode trust.
- Audit: every flip writes an ``audit_log`` row with the old + new
  intent so the dashboard "memory health" tile can plot
  drafting-session length distributions.

## Out of scope

- Mid-thread switch between two *different* drafted projects (the
  current model is one drafting session per thread).
- Cross-thread "resume drafting" UI (already a separate ticket on
  the sidebar work).
- Sub-flows for ``intent='shape_repo'`` / similar — those don't
  exist yet; E20 is shape_project-only.

## Acceptance

- Inline "Switch to drafting" CTA appears when classifier detects
  drafting intent + thread is not in drafting mode.
- Click flips ``thread.intent`` server-side, SSE pushes
  ``intent_change``, Console renders the "Drafting" badge.
- Inline "Exit drafting" CTA appears when classifier detects
  off-topic message + thread IS in drafting mode.
- Click flips ``thread.intent = None``, SSE pushes ``intent_change``,
  badge disappears.
- Audit log has one row per flip.
- E2E spec covers enter-then-exit in one thread (no archive,
  fact stream remains continuous).
"""


TICKETS: list[tuple[str, str]] = [
    (
        "E20-1: REST + SSE — intent flip endpoint",
        """\
**Goal.** New endpoint to flip a thread's ``intent`` in place +
SSE ``intent_change`` event so the UI rerenders without a refresh.

**Scope.**

- ``POST /v1/workspaces/{ws}/chat/active/intent`` with body
  ``{"intent": "shape_project" | null}``. Auth via session JWT;
  affects only the caller's own active thread.
- 409 when the thread is archived or has been packed.
- Writes ``thread.intent`` + ``audit_log`` row carrying old + new
  intent + thread_id.
- SSE addition in ``POST /chat/stream`` reader: ``intent_change``
  event for any subsequent reader. The single-window chat
  re-renders the system-bar badge.

**Acceptance.**

- Unit tests: flip Open→Drafting, Drafting→None, 409 on archived.
- ``audit_log`` rows present after each flip.
- SSE reader on the next stream emits ``intent_change`` once.
""",
    ),
    (
        "E20-2: Drafting-intent classifier",
        """\
**Goal.** Tiny LLM call (or pattern match) that says "yes, the user
is asking to shape a new project" / "no, they're asking about
something else". Reuses the topic-shift classifier output.

**Scope.**

- ``DraftingIntentService.classify(message, thread, recent_window)``
  returns a verdict + confidence: ``ENTER`` / ``EXIT`` / ``NEUTRAL``.
- Runs alongside ``classify_shift`` on each user message when the
  thread is in a sensible state (not too short, classifier
  enabled).
- Cost-gated: explicit keyword patterns ("давай сделаем проект",
  "let's shape", "shape a project around") short-circuit the LLM
  call; the LLM path runs only when patterns are ambiguous.

**Acceptance.**

- Unit tests for explicit-keyword fast path.
- LLM path mocked + asserted on the verdict shape.
- Failure → ``NEUTRAL`` (no CTA, no surprise mode flip).
""",
    ),
    (
        "E20-3: Inline CTA in single-window chat",
        """\
**Goal.** Console-side: when the SSE stream emits the classifier
verdict (E20-2), render an inline action card under the assistant's
message with [Switch to drafting] / [Keep chatting] buttons.

**Scope.**

- New SSE event type emitted by the backend: ``classifier_verdict``
  carrying the drafting-intent verdict. Backend emits at most one
  per user turn.
- Console handler in ``single-window-chat.tsx`` adds a new segment
  type ``IntentSuggestion`` rendered between deltas.
- Buttons POST to ``/api/chat/intent/flip`` → backend's E20-1
  endpoint.
- Exit CTA mirrors entry CTA but with inverse copy + target intent.

**Acceptance.**

- Visual smoke: e2e test stubs the SSE event + asserts the card
  renders.
- Click flips intent + the badge in the system bar updates within
  one second.
""",
    ),
    (
        "E20-4: E2E coverage for enter / exit pivot",
        """\
**Goal.** Playwright spec walking the full pivot loop.

**Scope.**

- Start in a normal chat (intent=None). Post a "shape a project
  around X" user message. Backend emits classifier_verdict=ENTER
  + assistant reply. Console renders the inline CTA. Click
  [Switch to drafting]. Badge updates. Same thread, no archive.
- Continue. Post an off-topic user message. Backend emits
  classifier_verdict=EXIT. Console renders inverse CTA. Click
  [Exit drafting]. Badge clears. Same thread.
- Validate: ``GET /chat/active`` shows the same thread id all
  the way through. ``audit_log`` has two entries with the right
  before/after intents.

**Acceptance.**

- E2E green on localhost (memory adapters profile).
- E2E green against prod (real Linear). Gated behind
  ``E2E_RUN_NAVIGATOR_STREAM=1`` because the LLM classifier
  burns tokens.
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
