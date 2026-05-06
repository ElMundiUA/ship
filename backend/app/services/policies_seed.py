"""Default workspace policies extracted from specialist prompts.

Source of truth for the prose-rule defaults Ship ships with — the rules
that used to live inside ``artifacts/patterns/role-*/ARTIFACT.md`` and
the Navigator's hard-coded system prompt. They land in two places:

- :func:`seed_default_policies` is called from the workspace
  provisioning paths (``backend/app/api/v1/routes/workspaces.py``) so
  every newly-created workspace lands with the standard policy set.
- The companion alembic migration ``0050_seed_default_policies``
  embeds the same content as a one-shot SQL backfill so workspaces
  that pre-date this refactor get the same defaults.

Both surfaces use ``(workspace_id, title)`` as the idempotency key —
re-runs (a re-provisioned workspace, alembic re-running on a partially
migrated DB) skip rules whose title already exists for the workspace.
That also lets admins safely *delete* a default rule and not have it
re-appear; we only insert what is missing, never reconcile.

Sort order strategy: globals first (group A, 0..9), cross-role next
(group B, 10..19), single-role last (group C, 20+ grouped by role).
The Console list inherits this ordering by default; admins can drag to
re-order locally.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.policies import WorkspacePolicy


@dataclass(frozen=True)
class SeedPolicy:
    """One default policy, ready to materialise as a ``WorkspacePolicy``.

    ``applies_to_roles=()`` (the default) means a global rule that
    renders for every role and for the Navigator chat. A non-empty
    tuple scopes the rule to the listed slugs (matching ``spec.role``
    in artifact frontmatter, plus the synthetic ``"navigator"`` slug
    for the chat surface).
    """

    title: str
    body: str
    applies_to_roles: tuple[str, ...] = ()
    sort_order: int = 0


# ---------------------------------------------------------------------------
# Group A — globals (extracted from common-base + new no-fabrication rule)
# ---------------------------------------------------------------------------


_GLOBAL_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Tracker writes go through the finish endpoint",
        body=(
            "Do not run ``gh issue comment``, ``linear-cli``, direct "
            "Linear / Jira / GitHub API calls that write, or any MCP "
            "tool that writes to the tracker. Reading is fine; writing "
            "is not. Every write goes through Ship's finish endpoint."
        ),
        sort_order=0,
    ),
    SeedPolicy(
        title="One comment per pass",
        body=(
            "Each run produces at most one substantive markdown "
            "comment, posted via the ``comment`` field of the finish "
            "payload. End the comment with ``[Ship SDLC:{{ROLE}}]`` so "
            "re-picks can detect \"already done\"."
        ),
        sort_order=1,
    ),
    SeedPolicy(
        title="Idempotency on re-run",
        body=(
            "Before doing work, re-read the ticket. If a comment with "
            "``[Ship SDLC:{{ROLE}}]`` already reflects the current "
            "state and there are no new inputs, finish with "
            "``outcome=ready_next_step`` and **no** ``comment`` so the "
            "run doesn't double-fire."
        ),
        sort_order=2,
    ),
    SeedPolicy(
        title="No merging, no Done without human approval",
        body=(
            "Do not merge PRs. Do not move tickets to Done without an "
            "explicit human approval signal — that's "
            "``outcome=needs_clarification`` with a question, not "
            "``outcome=ready_next_step``."
        ),
        sort_order=3,
    ),
    SeedPolicy(
        title="Branch only when you change code",
        body=(
            "Branchless roles (intake, BA, planner, architect, gap "
            "analyser, clarification, PM) call finish and stop — do "
            "not create empty branches or commit placeholder files. "
            "Branchful roles push code on the branch Ship CLI named "
            "for them, then call finish."
        ),
        sort_order=4,
    ),
    SeedPolicy(
        title="One ticket → one open PR",
        body=(
            "One ticket → one open PR. The branch name is set by Ship "
            "CLI at launch; do not create parallel branches for the "
            "same ticket. If two PRs already exist for the same "
            "ticket, leave the older one open and finish with "
            "``outcome=blocked`` describing the conflict."
        ),
        sort_order=5,
    ),
    SeedPolicy(
        title="Don't invent values you can't verify",
        body=(
            "Don't invent identifiers, names, paths, file or function "
            "references, ticket fields, label names, statuses, "
            "attributions, dates, or any other value you can't "
            "verify. If you need a value, fetch or read it. If you "
            "can't, say so explicitly and stop. Plausible-sounding "
            "guesses are forbidden — they're the single biggest source "
            "of operator-erosion and it's better to surface \"I don't "
            "know\" than a polished lie."
        ),
        sort_order=6,
    ),
)


# ---------------------------------------------------------------------------
# Group B — cross-role (reviewer / auditor patterns shared by 3+ roles)
# ---------------------------------------------------------------------------


_REVIEWER_ROLES: tuple[str, ...] = (
    "designer",
    "mobile-reviewer",
    "desktop-reviewer",
    "ml-reviewer",
    "game-balance-reviewer",
)
_AUDIT_ROLES: tuple[str, ...] = (
    "qa-reviewer",
    "tech-reviewer",
    "security-officer",
    "process-reviewer",
)
_REVIEWER_AND_AUDIT_ROLES: tuple[str, ...] = _REVIEWER_ROLES + _AUDIT_ROLES


_CROSS_ROLE_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Reviewers comment, never approve",
        body=(
            "Reviewer roles never approve a PR. Comment only, and "
            "request changes when at least one **blocking** finding "
            "is present. PR approval is a human signal."
        ),
        applies_to_roles=_REVIEWER_ROLES,
        sort_order=10,
    ),
    SeedPolicy(
        title="One anchored comment per PR",
        body=(
            "Post a single PR comment / review with a stable anchor "
            "for your role (e.g. ``design-review``, ``ml-review``, "
            "``mobile-review``, ``desktop-native-review``, "
            "``balance-review``). Update it on each push instead of "
            "stacking new ones."
        ),
        applies_to_roles=_REVIEWER_ROLES,
        sort_order=11,
    ),
    SeedPolicy(
        title="Findings need concrete evidence",
        body=(
            "Every finding cites concrete evidence: file path + line, "
            "the offending snippet, and a recommended replacement or "
            "canonical reference (design system, sklearn / TF / "
            "PyTorch / HuggingFace docs, Apple HIG / Android "
            "Architecture guides, etc.). Do not invent files, CVEs, "
            "metrics, or \"best practices\" without grounding in this "
            "repo."
        ),
        applies_to_roles=_REVIEWER_AND_AUDIT_ROLES,
        sort_order=12,
    ),
    SeedPolicy(
        title="De-dupe before opening audit tickets",
        body=(
            "Before creating a tracker ticket, search the target "
            "project for an open ticket with ``source:<your-role>`` "
            "or ``audit:auto`` covering the same area / spec / "
            "component. If one exists, do not create a duplicate; if "
            "needed, leave one comment on the existing card with the "
            "new fact."
        ),
        applies_to_roles=_AUDIT_ROLES,
        sort_order=13,
    ),
    SeedPolicy(
        title="Silence is the right outcome",
        body=(
            "If a pass produces no new, verifiable findings, do not "
            "create tickets and do not post a \"checkbox\" comment "
            "for the report. Silence is the correct outcome."
        ),
        applies_to_roles=_AUDIT_ROLES,
        sort_order=14,
    ),
)


# ---------------------------------------------------------------------------
# Group C — single-role
# ---------------------------------------------------------------------------


_DEVELOPER_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Work only on the API-provided branch",
        body=(
            "Implementation runs work only in the branch the API "
            "provides (e.g. ``fix/{{ISSUE}}-auto``). Do not create "
            "alternative ``feature/*`` branches for the same ticket "
            "— that produces duplicate PRs and manual cleanup."
        ),
        applies_to_roles=("developer",),
        sort_order=20,
    ),
    SeedPolicy(
        title="Tests required for new behaviour",
        body=(
            "Add or update unit / integration tests for new logic. "
            "If UX or a critical flow changes, update or add e2e "
            "(Playwright). Do not stop at a green ``test`` alone: new "
            "behaviour must be covered, or the PR / Linear comment "
            "must explicitly explain why (rare case)."
        ),
        applies_to_roles=("developer",),
        sort_order=21,
    ),
    SeedPolicy(
        title="Run all gates before opening the PR",
        body=(
            "Before opening a PR, run all relevant gates — ``lint``, "
            "``typecheck``, ``test``, ``build``, "
            "``test:e2e:smoke`` (chromium-desktop where applicable). "
            "All relevant targets must pass before the PR is opened."
        ),
        applies_to_roles=("developer",),
        sort_order=22,
    ),
    SeedPolicy(
        title="Commit message includes the ticket id",
        body=(
            "Commit messages use Conventional Commits with the "
            "ticket id: ``fix({{ISSUE}}): …`` or "
            "``feat({{ISSUE}}): …``."
        ),
        applies_to_roles=("developer",),
        sort_order=23,
    ),
    SeedPolicy(
        title="Open exactly one PR with `Closes` and move to In Review",
        body=(
            "Before opening a PR, check GitHub for an already-open "
            "PR for this ticket (body / title with "
            "``Closes {{ISSUE}}``, branch ``fix/{{ISSUE}}-auto`` or "
            "similar). If one exists, push to that branch or leave a "
            "single Linear comment with the PR link instead of "
            "opening a second. Otherwise, open exactly one PR with "
            "``Closes {{ISSUE}}`` and move Linear status to **In "
            "Review**."
        ),
        applies_to_roles=("developer",),
        sort_order=24,
    ),
)


_BA_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Rewrite the ticket via `description`, not `comment`",
        body=(
            "Use the ``description`` field on the finish payload to "
            "rewrite the ticket body. Do not paste the rewritten "
            "spec into the ``comment`` — ``comment`` carries the "
            "audit narration for the activity feed."
        ),
        applies_to_roles=("ba",),
        sort_order=25,
    ),
    SeedPolicy(
        title="Respect intake sections, append BA spec below",
        body=(
            "Keep the intake sections (Problem / Goal / Expected "
            "behaviour / Scope / AC / Non-goals / Risks) unchanged "
            "where they're correct; tighten where they aren't. "
            "Append the BA spec below them: Feature description, "
            "User stories, Acceptance criteria, Edge cases, Impacted "
            "components, Technical notes, Test plan."
        ),
        applies_to_roles=("ba",),
        sort_order=26,
    ),
    SeedPolicy(
        title="Don't push to implementation without a confirmed slice",
        body=(
            "If scope is too large for one delivery, finish with "
            "``outcome=needs_clarification`` and propose a split in "
            "the ``comment``. Do not set "
            "``stage_next=dev_implementation`` until a human "
            "confirms the slice."
        ),
        applies_to_roles=("ba",),
        sort_order=27,
    ),
)


_INTAKE_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Don't touch Backlog",
        body=(
            "Intake operates on tickets already in Todo and the "
            "pre-release project. Do not touch Backlog tickets — "
            "they aren't yours."
        ),
        applies_to_roles=("intake", "bug-triage"),
        sort_order=28,
    ),
    SeedPolicy(
        title="Rewrite the body via `description`, not `comment`",
        body=(
            "When a ticket is shape-ready, rewrite the body via the "
            "``description`` field on the finish payload. Use the "
            "section list defined for your role (intake: Problem / "
            "Goal / Expected / Scope / AC / Non-goals / Risks; "
            "bug-triage: Summary / Steps / Expected / Actual / "
            "Environment / Scope / Severity / Suspect / Workaround). "
            "Use the operator's original wording where it's already "
            "clear; tighten / restructure where it isn't. Do not "
            "paste the rewritten spec into the ``comment``."
        ),
        applies_to_roles=("intake", "bug-triage"),
        sort_order=29,
    ),
    SeedPolicy(
        title="Don't push forward when context is missing",
        body=(
            "When required context (goal / problem / expectation / "
            "AC / constraints / repro) is missing, finish with "
            "``outcome=needs_clarification`` and put numbered "
            "questions in the ``comment`` — don't push the ticket "
            "forward."
        ),
        applies_to_roles=("intake", "bug-triage"),
        sort_order=30,
    ),
)


_CLARIFICATION_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="No-op while waiting on the human",
        body=(
            "If the latest reply is from the agent and the human "
            "hasn't answered yet, do nothing — finish with no "
            "comment. One pass produces at most one follow-up "
            "comment, and only when the human has provided new input."
        ),
        applies_to_roles=("clarification",),
        sort_order=31,
    ),
)


_PRODUCT_MANAGER_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Triage routes, never implements",
        body=(
            "Triage routes work; it does not implement. Never "
            "silently change product scope — leave a triage comment "
            "when routing."
        ),
        applies_to_roles=("product-manager",),
        sort_order=32,
    ),
    SeedPolicy(
        title="Use existing labels, route to clarification when unsure",
        body=(
            "Use existing labels; never invent new ones during "
            "triage. When a ticket lacks a user story or acceptance "
            "criteria, route to ``role-clarification`` rather than "
            "guessing intent."
        ),
        applies_to_roles=("product-manager",),
        sort_order=33,
    ),
)


_SECURITY_OFFICER_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="New issues only in the security project, with priority mapping",
        body=(
            "New security issues are created only in the configured "
            "security project, status **Backlog**. Use the priority "
            "mapping: Snyk critical → 1 Urgent, high → 2 High, "
            "medium → 3 Medium, low → 4 Low."
        ),
        applies_to_roles=("security-officer",),
        sort_order=34,
    ),
    SeedPolicy(
        title="Don't invent vulnerabilities or fake JSON",
        body=(
            "If the Snyk JSON is missing, empty, or did not run, do "
            "not invent vulnerabilities and do not generate fake "
            "JSON. No tickets is the correct outcome."
        ),
        applies_to_roles=("security-officer",),
        sort_order=35,
    ),
)


_TECH_REVIEWER_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Tech-debt findings only in the tech-debt project",
        body=(
            "Architecture and tech-debt findings are created only in "
            "the configured tech-debt project, status **Backlog**."
        ),
        applies_to_roles=("tech-reviewer",),
        sort_order=36,
    ),
)


_REVIEWER_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Reviewer never pushes commits or modifies code",
        body=(
            "The reviewer role's only outputs are PR-line comments and "
            "an anchor summary comment. Never push commits, amend, "
            "rebase, force-push, or modify any file in the working "
            "tree. If a fix is obvious, describe it in the comment so "
            "the developer applies it on the next pass."
        ),
        applies_to_roles=("reviewer",),
        sort_order=42,
    ),
    SeedPolicy(
        title="Reviewer never approves; ready_next_step routes to a human",
        body=(
            "Approval is a human signal. Even when the diff is clean, "
            "do not click approve on the PR. Finish with "
            "``outcome=ready_next_step`` and ``stage_next=human_merge`` "
            "so the human reviewer takes over."
        ),
        applies_to_roles=("reviewer",),
        sort_order=43,
    ),
    SeedPolicy(
        title="One anchored review comment per pass",
        body=(
            "Post a single anchor summary comment with the "
            "``reviewer`` anchor; update it on subsequent passes "
            "instead of stacking new ones. Per-finding PR-line "
            "comments are separate and may stack with each finding."
        ),
        applies_to_roles=("reviewer",),
        sort_order=44,
    ),
)


_QA_PIPELINE_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="QA engineer never fixes defects, only reports them",
        body=(
            "Manual QA is read-only on the codebase. When a defect is "
            "found, finish with ``outcome=blocked`` and a structured "
            "defect list — never push a fix yourself. The developer "
            "owns the next pass."
        ),
        applies_to_roles=("qa-engineer",),
        sort_order=45,
    ),
    SeedPolicy(
        title="Automation tests anchor to the architect's plan",
        body=(
            "Each automated test added in the QA-automation pass "
            "anchors to a Given/When/Then scenario from the QA "
            "architect's test plan section. Do not invent new "
            "scenarios at this stage; if a gap appears, finish with "
            "``outcome=needs_clarification`` and describe it."
        ),
        applies_to_roles=("qa-automation",),
        sort_order=46,
    ),
)


_NAVIGATOR_POLICIES: tuple[SeedPolicy, ...] = (
    SeedPolicy(
        title="Never fabricate ids, names, or attribution",
        body=(
            "Never fabricate any identifier or attribution. That "
            "includes: repo paths, tickets, URLs, artifact ids, "
            "pipeline ids, integration names, user names, emails, "
            "GitHub / Linear / Slack logins, PR / commit / run "
            "authors, PR numbers, commit SHAs, version strings, "
            "timestamps, dates, file line counts, release notes. If "
            "a tool can produce the value, call it. If a tool's "
            "response is missing a field, surface the gap verbatim "
            "(\"the response doesn't include the author\") rather "
            "than inferring from a username elsewhere, the repo "
            "owner, or training data. When the user pushes back "
            "(\"are you sure\", \"that's wrong\"), call the tool "
            "that produces ground truth and answer from its result. "
            "\"I don't know\" / \"the data doesn't include that\" / "
            "\"no tool can answer that\" are valid, expected, "
            "**preferred** answers when the alternative would be "
            "guessing."
        ),
        applies_to_roles=("navigator",),
        sort_order=37,
    ),
    SeedPolicy(
        title="Time and workspace come from session context, not training data",
        body=(
            "Today's date and the active workspace id come from the "
            "**Session context** system message — use those for any "
            "\"today\" / \"yesterday\" / \"this week\" / \"last N "
            "days\" phrasing. Never assume the year or month from "
            "training data."
        ),
        applies_to_roles=("navigator",),
        sort_order=38,
    ),
    SeedPolicy(
        title="Propose first, confirm fleet-scope changes via ship-choice",
        body=(
            "For any tracker, inbox, or automation mutation, propose "
            "first and only execute on explicit user confirmation. "
            "For destructive or fleet-scope changes, confirm via the "
            "``ship-choice`` widget before calling."
        ),
        applies_to_roles=("navigator",),
        sort_order=39,
    ),
    SeedPolicy(
        title="Mutating tools require workspace admin",
        body=(
            "Mutating tools (``inbox_dispose``, ``inbox_snooze``, "
            "``inbox_reassign``, ``play_run_now``, "
            "``play_automate``, ``automation_toggle``, "
            "``inbox_routing_upsert``, ``archive_bucket_article``, "
            "``update_ticket``, ``set_priority_state``, "
            "``start_decomposition``) require workspace admin. If a "
            "call returns ``{\"error\": \"forbidden\"}``, explain "
            "that admin is required; don't retry."
        ),
        applies_to_roles=("navigator",),
        sort_order=40,
    ),
    SeedPolicy(
        title="Stay inside the current topic",
        body=(
            "Stay inside the current topic. Topic shifts are decided "
            "by the host, not the agent."
        ),
        applies_to_roles=("navigator",),
        sort_order=41,
    ),
)


_DEFAULT_POLICIES: tuple[SeedPolicy, ...] = (
    *_GLOBAL_POLICIES,
    *_CROSS_ROLE_POLICIES,
    *_DEVELOPER_POLICIES,
    *_BA_POLICIES,
    *_INTAKE_POLICIES,
    *_CLARIFICATION_POLICIES,
    *_PRODUCT_MANAGER_POLICIES,
    *_SECURITY_OFFICER_POLICIES,
    *_TECH_REVIEWER_POLICIES,
    *_REVIEWER_POLICIES,
    *_QA_PIPELINE_POLICIES,
    *_NAVIGATOR_POLICIES,
)


def default_policies() -> tuple[SeedPolicy, ...]:
    """Return the canonical default policy set (read-only snapshot)."""
    return _DEFAULT_POLICIES


async def seed_default_policies(
    *,
    session: AsyncSession,
    workspace_id: uuid.UUID,
    policies: Sequence[SeedPolicy] | None = None,
) -> dict:
    """Insert default policies for ``workspace_id``, idempotent on title.

    Reads the workspace's current policy titles in one query, then
    inserts only those defaults whose title isn't already present.
    The contract is **ENSURE**: re-runs against a fully-seeded
    workspace are no-ops, but a default an admin *deleted* will be
    re-inserted on the next call. Admins who want to permanently
    silence a default should toggle ``enabled=False`` instead of
    deleting — the disabled row keeps the title slot taken so the
    seeder leaves it alone.

    ``policies`` is overrideable for tests and for the alembic
    backfill that wants to pin a historical snapshot.

    Returns ``{"inserted": <int>, "skipped": <int>}`` for audit-log
    enrichment by callers.
    """
    catalog = tuple(policies) if policies is not None else _DEFAULT_POLICIES
    if not catalog:
        return {"inserted": 0, "skipped": 0}

    existing_rows = await session.execute(
        select(WorkspacePolicy.title).where(
            WorkspacePolicy.workspace_id == workspace_id
        )
    )
    existing_titles = {row[0] for row in existing_rows.all()}

    inserted = 0
    skipped = 0
    for policy in catalog:
        if policy.title in existing_titles:
            skipped += 1
            continue
        session.add(
            WorkspacePolicy(
                workspace_id=workspace_id,
                title=policy.title,
                body=policy.body,
                enabled=True,
                sort_order=policy.sort_order,
                applies_to_roles=(
                    list(policy.applies_to_roles)
                    if policy.applies_to_roles
                    else None
                ),
            )
        )
        inserted += 1

    if inserted:
        await session.flush()
    return {"inserted": inserted, "skipped": skipped}


__all__ = [
    "SeedPolicy",
    "default_policies",
    "seed_default_policies",
]
