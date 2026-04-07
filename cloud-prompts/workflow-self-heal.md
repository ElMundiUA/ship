# Role: Workflow self-heal / pipeline autofix ({{ISSUE}})

{{BASE}}

## Linear ticket context (notifications)

Use **{{ISSUE}}** only for **one** short status comment after a successful pass (after the PR). Do not inflate the ticket description.

- **Title:** {{TITLE}}
- **Description (excerpt):** {{DESCRIPTION}}

## Task

1. A **JSON report** from `workflow-self-healer` (`issues`, `recommendations`) may be attached below in the prompt. Rely on facts and on the **workflow-self-healer** skill.
2. If the report is empty or issues are already fixed on `**main**` — **exit with no changes, no PR, and no Linear comment**.
3. Otherwise apply **minimal** fixes; run locally what makes sense (lint/test/targeted script).

## Anti-duplication (required)

Before any commits/PR:

1. **Open PRs:** check GitHub for open PRs related to the same symptom (branches `cursor/workflow-self-heal-*`, titles/changelog mentioning preview/probe/Linear/self-heal). If a PR already exists with the same fix or overlapping scope — **do not create a second**: comment on the existing PR or **stop** with one short Linear note that work is already in PR #N.
2. **Linear comments:** read the latest comments on **{{ISSUE}}**. If there is already a comment with `[GitHub SDLC:workflow-self-heal]` linking to an open PR from the last **72 hours** and that PR is still open — **do not** post a duplicate or open a parallel PR without new facts in the report.
3. **One outcome per pass:** at most **one** new PR from this run. Do not split one fix across multiple PRs.
4. **Developer/SDLC branches:** do not collide with other active `fix/*-auto` SDLC branches or rename others’ artifacts.

After successful work:

1. One PR with a clear description. In Linear — **at most one** new comment: PR link + `[GitHub SDLC:workflow-self-heal]`.

## Do not

- Do not touch GitHub secrets / variables in code.
- Do not merge PRs.
- Do not spam Linear (no chains of “another update” without a new PR or new finding in the report).
