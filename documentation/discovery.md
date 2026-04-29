# Discovery contract

Discovery is the first conversation before Ship changes a repo. Use it when an agent, developer, or platform engineer is bringing a repository into a workspace for the first time.

The goal is not to collect every possible detail. The goal is to record enough intent, scope, and evidence expectations that setup can be reviewed later.

## Who uses this page

- Product owners use it to make sure the workspace matches how the team decides work.
- Engineers use it to avoid guessing tracker, CI, agent, and secret setup.
- Agents use it as a stop condition: do not write files until the required answers are known.

If the workspace is already wired, use [Operating](./operating.md) instead.

## Phase 0 — inspect what already exists

Before asking questions, inspect the repo and workspace:

- Is there already a `.ship/config.yml`?
- Which agents have on-disk markers?
- Which CI provider is present?
- Which tracker references appear in branches, PRs, or issue links?
- Are there existing knowledge files under `.ship/knowledge/`?

Use `shipctl doctor` when local CLI access is available. Treat its output as evidence to confirm, not as permission to proceed silently.

## Phase 1 — confirm ownership and scope

Ask these questions in plain language:

1. Which product area or team owns this workspace?
2. Which repos should Ship observe or help wire?
3. Which tracker is the record of product intent?
4. Which states mean “not ready”, “ready”, “active”, “blocked”, “review”, and “done”?
5. Who can approve risky changes or policy exceptions?
6. Which agents may be used, and where should their rules live?
7. Which secrets are required, and which secret store owns them?
8. Which product or repo facts should become knowledge before the first meaningful run?

Record confirmed answers in the setup PR, onboarding note, or workspace record.

## Phase 2 — propose the setup

Give one recommended setup and, if useful, one stricter alternative. Each proposal should name:

- repos to activate;
- tracker and owner mapping;
- knowledge to seed;
- agent rule targets;
- automations to enable now versus later;
- evidence each automation must leave;
- human follow-up needed for secrets or permissions.

Do not hide uncertainty. If an answer is unknown, leave it as a blocker or Inbox item rather than guessing.

## Phase 3 — implement through reviewable changes

When the human accepts the setup:

- connect or activate the repo through the console where possible;
- install or refresh versioned agent rules through the supported CLI path;
- add knowledge files or articles through reviewable changes;
- configure secrets through the platform, not committed files;
- open PRs for repo changes instead of mutating hidden state;
- record which artifacts, prompts, rules, or templates were consumed.

The setup should leave enough evidence that a reviewer can see what was installed and why.

## Phase 4 — validate

Before calling setup complete, show:

- workspace and repo activation status;
- tracker binding status;
- knowledge seeded or intentionally deferred;
- secrets configured or still waiting on a human;
- local `shipctl verify` result when CLI wiring changed;
- any Inbox items created for unresolved decisions;
- rollback path for generated repo changes.

## Non-negotiable rules

- No secret commits.
- No silent assumptions about tracker states, ownership, or release gates.
- No automation without a named scope and evidence destination.
- No agent rules that cannot be reviewed or rolled back.
- No claim of success without a visible record.

## Where to next

Use [Getting started](/getting-started) for the product flow, [Configuration](./configuration.md) for local files, [Knowledge](./knowledge-buckets.md) for context, and [Troubleshooting](./troubleshooting.md) when validation fails.
