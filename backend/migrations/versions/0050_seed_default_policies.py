"""seed default workspace_policies extracted from specialist prompts.

Revision ID: 0050_seed_default_policies
Revises: 0049_policy_role_scope
Create Date: 2026-05-03

One-shot backfill that lands the standard policy set in every existing
workspace. Mirrors the data in
``backend/app/services/policies_seed.py``, which is the canonical
seeder for *new* workspaces (called from the workspace-creation paths
in ``api/v1/routes/workspaces.py``). This migration captures the
**snapshot at refactor time** so workspaces that pre-date the
extraction don't lose the rules when step 6 strips them out of the
``role-*/ARTIFACT.md`` patterns.

Design choices
--------------

- **Pure SQL** (no app imports) — mirrors ``0016_backfill_summary_articles``
  and ``0032_inbox_backfill_legacy``. The migration must replay on a
  fresh database years from now even if ``policies_seed.py`` is later
  refactored or renamed; freezing the data inline guarantees that.
- **Idempotency by ``(workspace_id, title)``** via ``NOT EXISTS``. Re-
  running ``alembic upgrade head`` is a no-op for already-seeded
  rows. Admins can also delete a default rule and not have it
  regenerated.
- **One ``INSERT ... SELECT`` per policy** rather than a 34-row
  ``VALUES`` block — keeps each statement small and the diff
  reviewable, and lets us pass the array literal through bind params
  cleanly instead of wrestling with Postgres array literal escaping.
- **No-op when ``workspaces`` is empty** (``CROSS JOIN`` against an
  empty table inserts zero rows), so the migration is safe to run on
  a brand-new database where workspace creation will go through the
  Python seeder.

Downgrade removes only the policies that look like seeded defaults —
matched by ``(workspace_id, title)`` against the same set the upgrade
inserted. Admin-edited bodies are preserved intact (we match on title,
not body), so an admin who renamed a rule will keep the renamed copy
on downgrade.
"""

from __future__ import annotations

from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0050_seed_default_policies"
down_revision: Union[str, None] = "0049_policy_role_scope"
branch_labels = None
depends_on = None


# Frozen snapshot of the default policy set. Mirrors
# ``policies_seed._DEFAULT_POLICIES`` at refactor time. ``applies_to_roles
# is None`` means a global rule (renders for every role + Navigator
# chat); a non-empty list scopes the rule to the listed slugs.
_BACKFILL_POLICIES: tuple[dict, ...] = (
    {
        "title": "Tracker writes go through the finish endpoint",
        "body": "Do not run ``gh issue comment``, ``linear-cli``, direct Linear / Jira / GitHub API calls that write, or any MCP tool that writes to the tracker. Reading is fine; writing is not. Every write goes through Ship's finish endpoint.",
        "applies_to_roles": None,
        "sort_order": 0,
    },
    {
        "title": "One comment per pass",
        "body": 'Each run produces at most one substantive markdown comment, posted via the ``comment`` field of the finish payload. End the comment with ``[Ship SDLC:{{ROLE}}]`` so re-picks can detect "already done".',
        "applies_to_roles": None,
        "sort_order": 1,
    },
    {
        "title": "Idempotency on re-run",
        "body": "Before doing work, re-read the ticket. If a comment with ``[Ship SDLC:{{ROLE}}]`` already reflects the current state and there are no new inputs, finish with ``outcome=ready_next_step`` and **no** ``comment`` so the run doesn't double-fire.",
        "applies_to_roles": None,
        "sort_order": 2,
    },
    {
        "title": "No merging, no Done without human approval",
        "body": "Do not merge PRs. Do not move tickets to Done without an explicit human approval signal — that's ``outcome=needs_clarification`` with a question, not ``outcome=ready_next_step``.",
        "applies_to_roles": None,
        "sort_order": 3,
    },
    {
        "title": "Branch only when you change code",
        "body": "Branchless roles (intake, BA, planner, architect, gap analyser, clarification, PM) call finish and stop — do not create empty branches or commit placeholder files. Branchful roles push code on the branch Ship CLI named for them, then call finish.",
        "applies_to_roles": None,
        "sort_order": 4,
    },
    {
        "title": "One ticket → one open PR",
        "body": "One ticket → one open PR. The branch name is set by Ship CLI at launch; do not create parallel branches for the same ticket. If two PRs already exist for the same ticket, leave the older one open and finish with ``outcome=blocked`` describing the conflict.",
        "applies_to_roles": None,
        "sort_order": 5,
    },
    {
        "title": "Don't invent values you can't verify",
        "body": 'Don\'t invent identifiers, names, paths, file or function references, ticket fields, label names, statuses, attributions, dates, or any other value you can\'t verify. If you need a value, fetch or read it. If you can\'t, say so explicitly and stop. Plausible-sounding guesses are forbidden — they\'re the single biggest source of operator-erosion and it\'s better to surface "I don\'t know" than a polished lie.',
        "applies_to_roles": None,
        "sort_order": 6,
    },
    {
        "title": "Reviewers comment, never approve",
        "body": "Reviewer roles never approve a PR. Comment only, and request changes when at least one **blocking** finding is present. PR approval is a human signal.",
        "applies_to_roles": ["designer", "mobile-reviewer", "desktop-reviewer", "ml-reviewer", "game-balance-reviewer"],
        "sort_order": 10,
    },
    {
        "title": "One anchored comment per PR",
        "body": "Post a single PR comment / review with a stable anchor for your role (e.g. ``design-review``, ``ml-review``, ``mobile-review``, ``desktop-native-review``, ``balance-review``). Update it on each push instead of stacking new ones.",
        "applies_to_roles": ["designer", "mobile-reviewer", "desktop-reviewer", "ml-reviewer", "game-balance-reviewer"],
        "sort_order": 11,
    },
    {
        "title": "Findings need concrete evidence",
        "body": 'Every finding cites concrete evidence: file path + line, the offending snippet, and a recommended replacement or canonical reference (design system, sklearn / TF / PyTorch / HuggingFace docs, Apple HIG / Android Architecture guides, etc.). Do not invent files, CVEs, metrics, or "best practices" without grounding in this repo.',
        "applies_to_roles": ["designer", "mobile-reviewer", "desktop-reviewer", "ml-reviewer", "game-balance-reviewer", "qa-architect", "tech-architect", "security-officer"],
        "sort_order": 12,
    },
    {
        "title": "De-dupe before opening audit tickets",
        "body": "Before creating a tracker ticket, search the target project for an open ticket with ``source:<your-role>`` or ``audit:auto`` covering the same area / spec / component. If one exists, do not create a duplicate; if needed, leave one comment on the existing card with the new fact.",
        "applies_to_roles": ["qa-architect", "tech-architect", "security-officer"],
        "sort_order": 13,
    },
    {
        "title": "Silence is the right outcome",
        "body": 'If a pass produces no new, verifiable findings, do not create tickets and do not post a "checkbox" comment for the report. Silence is the correct outcome.',
        "applies_to_roles": ["qa-architect", "tech-architect", "security-officer"],
        "sort_order": 14,
    },
    {
        "title": "Work only on the API-provided branch",
        "body": "Implementation runs work only in the branch the API provides (e.g. ``fix/{{ISSUE}}-auto``). Do not create alternative ``feature/*`` branches for the same ticket — that produces duplicate PRs and manual cleanup.",
        "applies_to_roles": ["developer"],
        "sort_order": 20,
    },
    {
        "title": "Tests required for new behaviour",
        "body": "Add or update unit / integration tests for new logic. If UX or a critical flow changes, update or add e2e (Playwright). Do not stop at a green ``test`` alone: new behaviour must be covered, or the PR / Linear comment must explicitly explain why (rare case).",
        "applies_to_roles": ["developer"],
        "sort_order": 21,
    },
    {
        "title": "Run all gates before opening the PR",
        "body": "Before opening a PR, run all relevant gates — ``lint``, ``typecheck``, ``test``, ``build``, ``test:e2e:smoke`` (chromium-desktop where applicable). All relevant targets must pass before the PR is opened.",
        "applies_to_roles": ["developer"],
        "sort_order": 22,
    },
    {
        "title": "Commit message includes the ticket id",
        "body": "Commit messages use Conventional Commits with the ticket id: ``fix({{ISSUE}}): …`` or ``feat({{ISSUE}}): …``.",
        "applies_to_roles": ["developer"],
        "sort_order": 23,
    },
    {
        "title": "Open exactly one PR with `Closes` and move to In Review",
        "body": "Before opening a PR, check GitHub for an already-open PR for this ticket (body / title with ``Closes {{ISSUE}}``, branch ``fix/{{ISSUE}}-auto`` or similar). If one exists, push to that branch or leave a single Linear comment with the PR link instead of opening a second. Otherwise, open exactly one PR with ``Closes {{ISSUE}}`` and move Linear status to **In Review**.",
        "applies_to_roles": ["developer"],
        "sort_order": 24,
    },
    {
        "title": "Rewrite the ticket via `description`, not `comment`",
        "body": "Use the ``description`` field on the finish payload to rewrite the ticket body. Do not paste the rewritten spec into the ``comment`` — ``comment`` carries the audit narration for the activity feed.",
        "applies_to_roles": ["ba"],
        "sort_order": 25,
    },
    {
        "title": "Respect intake sections, append BA spec below",
        "body": "Keep the intake sections (Problem / Goal / Expected behaviour / Scope / AC / Non-goals / Risks) unchanged where they're correct; tighten where they aren't. Append the BA spec below them: Feature description, User stories, Acceptance criteria, Edge cases, Impacted components, Technical notes, Test plan.",
        "applies_to_roles": ["ba"],
        "sort_order": 26,
    },
    {
        "title": "Don't push to implementation without a confirmed slice",
        "body": "If scope is too large for one delivery, finish with ``outcome=needs_clarification`` and propose a split in the ``comment``. Do not set ``stage_next=dev_implementation`` until a human confirms the slice.",
        "applies_to_roles": ["ba"],
        "sort_order": 27,
    },
    {
        "title": "Don't touch Backlog",
        "body": "Intake operates on tickets already in Todo and the pre-release project. Do not touch Backlog tickets — they aren't yours.",
        "applies_to_roles": ["intake"],
        "sort_order": 28,
    },
    {
        "title": "Rewrite the body via `description`, not `comment`",
        "body": "When a ticket is shape-ready, rewrite the body via the ``description`` field on the finish payload. Sections, in order: Problem, Goal, Expected behaviour, Scope, Acceptance criteria, Non-goals, Risks. Use the operator's original wording where it's already clear; tighten / restructure where it isn't. Do not paste the rewritten spec into the ``comment``.",
        "applies_to_roles": ["intake"],
        "sort_order": 29,
    },
    {
        "title": "Don't push forward when context is missing",
        "body": "When required context (goal / problem / expectation / AC / constraints) is missing, finish with ``outcome=needs_clarification`` and put numbered questions in the ``comment`` — don't push the ticket forward.",
        "applies_to_roles": ["intake"],
        "sort_order": 30,
    },
    {
        "title": "No-op while waiting on the human",
        "body": "If the latest reply is from the agent and the human hasn't answered yet, do nothing — finish with no comment. One pass produces at most one follow-up comment, and only when the human has provided new input.",
        "applies_to_roles": ["clarification"],
        "sort_order": 31,
    },
    {
        "title": "Triage routes, never implements",
        "body": "Triage routes work; it does not implement. Never silently change product scope — leave a triage comment when routing.",
        "applies_to_roles": ["product-manager"],
        "sort_order": 32,
    },
    {
        "title": "Use existing labels, route to clarification when unsure",
        "body": "Use existing labels; never invent new ones during triage. When a ticket lacks a user story or acceptance criteria, route to ``role-clarification`` rather than guessing intent.",
        "applies_to_roles": ["product-manager"],
        "sort_order": 33,
    },
    {
        "title": "New issues only in the security project, with priority mapping",
        "body": "New security issues are created only in the configured security project, status **Backlog**. Use the priority mapping: Snyk critical → 1 Urgent, high → 2 High, medium → 3 Medium, low → 4 Low.",
        "applies_to_roles": ["security-officer"],
        "sort_order": 34,
    },
    {
        "title": "Don't invent vulnerabilities or fake JSON",
        "body": "If the Snyk JSON is missing, empty, or did not run, do not invent vulnerabilities and do not generate fake JSON. No tickets is the correct outcome.",
        "applies_to_roles": ["security-officer"],
        "sort_order": 35,
    },
    {
        "title": "Tech-debt findings only in the tech-debt project",
        "body": "Architecture and tech-debt findings are created only in the configured tech-debt project, status **Backlog**.",
        "applies_to_roles": ["tech-architect"],
        "sort_order": 36,
    },
    {
        "title": "Never fabricate ids, names, or attribution",
        "body": 'Never fabricate any identifier or attribution. That includes: repo paths, tickets, URLs, artifact ids, pipeline ids, integration names, user names, emails, GitHub / Linear / Slack logins, PR / commit / run authors, PR numbers, commit SHAs, version strings, timestamps, dates, file line counts, release notes. If a tool can produce the value, call it. If a tool\'s response is missing a field, surface the gap verbatim ("the response doesn\'t include the author") rather than inferring from a username elsewhere, the repo owner, or training data. When the user pushes back ("are you sure", "that\'s wrong"), call the tool that produces ground truth and answer from its result. "I don\'t know" / "the data doesn\'t include that" / "no tool can answer that" are valid, expected, **preferred** answers when the alternative would be guessing.',
        "applies_to_roles": ["navigator"],
        "sort_order": 37,
    },
    {
        "title": "Time and workspace come from session context, not training data",
        "body": 'Today\'s date and the active workspace id come from the **Session context** system message — use those for any "today" / "yesterday" / "this week" / "last N days" phrasing. Never assume the year or month from training data.',
        "applies_to_roles": ["navigator"],
        "sort_order": 38,
    },
    {
        "title": "Propose first, confirm fleet-scope changes via ship-choice",
        "body": "For any tracker, inbox, or automation mutation, propose first and only execute on explicit user confirmation. For destructive or fleet-scope changes, confirm via the ``ship-choice`` widget before calling.",
        "applies_to_roles": ["navigator"],
        "sort_order": 39,
    },
    {
        "title": "Mutating tools require workspace admin",
        "body": 'Mutating tools (``inbox_dispose``, ``inbox_snooze``, ``inbox_reassign``, ``play_run_now``, ``play_automate``, ``automation_toggle``, ``inbox_routing_upsert``, ``archive_bucket_article``) require workspace admin. If a call returns ``{"error": "forbidden"}``, explain that admin is required; don\'t retry.',
        "applies_to_roles": ["navigator"],
        "sort_order": 40,
    },
    {
        "title": "Stay inside the current topic",
        "body": "Stay inside the current topic. Topic shifts are decided by the host, not the agent.",
        "applies_to_roles": ["navigator"],
        "sort_order": 41,
    },
)


_INSERT_SQL = sa.text(
    """
    INSERT INTO workspace_policies
        (workspace_id, title, body, applies_to_roles, sort_order, enabled)
    SELECT w.id, :title, :body, :applies_to_roles, :sort_order, true
    FROM workspaces w
    WHERE NOT EXISTS (
        SELECT 1 FROM workspace_policies wp
        WHERE wp.workspace_id = w.id AND wp.title = :title
    )
    """
).bindparams(
    # Pin every bind to the matching column type. Without this, psycopg
    # gets ambiguous-parameter errors when the same ``:title`` is
    # consumed by an INSERT column (varchar(160)) and a WHERE compare
    # (varchar(160) vs text inferred), and ``applies_to_roles`` falls
    # back to a generic ``VARCHAR[]`` cast Postgres rejects against the
    # ``VARCHAR(64)[]`` column.
    sa.bindparam("title", type_=sa.String(length=160)),
    sa.bindparam("body", type_=sa.Text()),
    sa.bindparam(
        "applies_to_roles",
        type_=postgresql.ARRAY(sa.String(length=64)),
    ),
    sa.bindparam("sort_order", type_=sa.Integer()),
)


_DELETE_SQL = sa.text(
    """
    DELETE FROM workspace_policies
    WHERE title = :title
    """
)


def upgrade() -> None:
    conn = op.get_bind()
    for policy in _BACKFILL_POLICIES:
        conn.execute(_INSERT_SQL, parameters=policy)


def downgrade() -> None:
    conn = op.get_bind()
    for policy in _BACKFILL_POLICIES:
        conn.execute(_DELETE_SQL, parameters={"title": policy["title"]})
