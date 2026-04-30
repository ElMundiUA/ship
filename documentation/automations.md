# Automations

An automation is repeatable work that runs under explicit rules. It might review a pull request, check release readiness, inspect dependencies, seed knowledge, or open a finding. The important part is not that a machine acted; the important part is that the action was bounded, visible, and tied to evidence.

## What an automation needs

Every automation should answer five plain questions:

| Question | Why it matters |
| --- | --- |
| What may run? | The procedure, check, or agent-assisted action must be named. |
| Where may it run? | Scope can be one repo, selected repos, or the whole workspace. |
| When may it run? | Trigger can be manual, scheduled, or event-driven. |
| Who owns the result? | A human or team remains responsible for risk and next steps. |
| What evidence is left? | The result must link to tickets, pull requests, checks, comments, or Inbox items. |

If you cannot answer those questions, do not automate the work yet. Tighten the workflow first.

## The safe default

Ship follows the book's posture:

- Humans own intent.
- Machines run inside fences.
- Empty or skipped work is acceptable when the rules say nothing is eligible.
- One clear owner is better than a queue where everyone assumes someone else will decide.
- Evidence matters more than volume.

That means an automation should be small enough to explain in one sentence and predictable enough to review after it runs.

## Common automation shapes

### Pull request review

Use for repeatable review checks that should attach evidence to a PR: self-review, policy checks, release notes, or change-risk summaries.

Good evidence: PR comment, check result, linked ticket, and any follow-up Inbox item.

### Knowledge refresh

Use when repo facts or product context need to be seeded or refreshed. The goal is to reduce repeated clarifications, not to hide decisions in generated prose.

Good evidence: knowledge article version, source path, provenance, and reviewer approval when needed.

### Audit pass

Use for security, dependency, QA, or architecture findings that should not compete with product delivery in the same queue.

Good evidence: finding ticket, report link, affected repo, severity, and owner.

### Release readiness

Use before a promotion or release window to gather checks and gaps. This should inform a human decision, not silently declare production safe.

Good evidence: release checklist, CI links, rollback note, and final approval.

## Inbox behavior

Automations should escalate only when a human decision is useful. Typical Inbox items are:

- a missing fact blocks progress;
- a proposed improvement needs approval;
- a repeated failure needs ownership;
- a policy exception needs review;
- a release or risky change needs explicit consent.

Do not use the Inbox as a log stream. If no decision is needed, leave evidence on the original ticket, PR, check, or dashboard row.

## Technical reference

Developers may see automations represented as routines, generated workflows, callback payloads, and pipeline records. That layer is intentionally technical and belongs in [Configuration](./configuration.md), [Operating](./operating.md), and the [CLI reference](./configuration.md#local-commands).

New user-facing examples should avoid legacy terms such as `lane`, `run`, and `pipeline_run` unless the page is explaining the implementation.

## Checklist before enabling an automation

- The scope is explicit.
- The trigger is explicit.
- The owner is explicit.
- The evidence destination is explicit.
- Secrets live in the host or Ship secret store, not in prompts.
- A failed or empty execution leaves a readable reason.
- The related prompt, rule, or artifact can be reviewed and rolled back.

## Where to next

Read [Concepts](./concepts.md) for the product vocabulary, [Knowledge](./knowledge-buckets.md) for context, [Operating](./operating.md) for day-to-day review, and [Configuration](./configuration.md) for the repo-level fields that developers maintain.
