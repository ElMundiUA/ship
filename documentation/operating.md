# Operating

Operating Ship means keeping the delivery story readable. Start from the console, not from a terminal: check workspace health, review active work, resolve Inbox items, inspect knowledge drift, and only then drop into CLI diagnostics when repo wiring needs attention.

## Daily review

Use this order when you open the workspace:

1. **Workspace health.** Look for degraded status, failed pipeline signals, broken automations, or repos behind the current Ship bundle.
2. **Work in progress.** Confirm active work has tracker context, repo context, and a clear owner.
3. **Inbox.** Resolve items that need a human decision: approvals, failures, clarifications, improvements, and exceptions.
4. **Shipped work.** Review merged features, fixes, and rollbacks from the last 24 hours.
5. **Knowledge.** Check whether repeated questions or failures point to missing repo or product context.

The goal is not to maximize activity. The goal is to keep work explainable.

## Drain the Inbox

Treat Inbox items as decision work, not notifications.

- Handle failures and approvals first because they block other work.
- Answer clarifications when the answer changes what an agent or reviewer can do.
- Accept, decline, or defer improvements with a reason.
- Reassign when routing is wrong; do not silently leave work in the wrong queue.
- Snooze only when a specific future condition will change the answer.

When the same item type repeats, fix the system: update knowledge, routing, automation scope, or the artifact that produced the behavior.

## Review evidence

For any important action, ask:

- Which tracker item or product decision started this?
- Which repo and PR carry the change?
- Which checks ran?
- Which human approved, rejected, or deferred the result?
- Which knowledge or policy entry explains the constraint?

If you cannot answer those questions from links in the workspace, the trail is too weak.

## Keep knowledge fresh

Use knowledge updates when context repeats:

- code style or test command changed;
- a product rule keeps being explained in comments;
- an integration needs a runbook;
- a clarification item repeats across similar work;
- a policy changed and prompts should not carry the old rule.

Keep articles short and sourced. A knowledge article should be easier to review than a long prompt.

## Use CLI diagnostics when repo wiring is the problem

The CLI is the workbench for developers and platform teams.

```bash
shipctl doctor
shipctl verify
shipctl sync
shipctl config validate
```

Use `doctor` to understand what the repo looks like. Use `verify` to check whether the current Ship setup is healthy. Use `sync` to refresh artifacts. Use `config validate` before merging hand edits.

## Common operating moves

### A repo needs a Ship bundle update

Open the workspace banner or repo configuration, review the generated changes, and merge the update through a PR. Confirm the repo no longer appears in the update-needed list.

### Work is active without a tracker key

Find whether the PR or branch missed the naming contract, or whether the tracker binding is missing. Fix the link before treating the work as evidence.

### An automation keeps failing

Do not keep retrying blindly. Check the latest evidence, identify whether the failure is missing context, bad credentials, wrong scope, or a broken procedure, then either resolve the Inbox item or disable the automation until the contract is corrected.

### A clarification repeats

Turn the answer into knowledge or policy. Repeated clarification is usually a missing context problem, not a reason to train people to answer the same question forever.

### The dashboard looks quiet

Quiet is not automatically bad. Confirm connected repos, tracker binding, and recent evidence. If there is no eligible work, a quiet workspace is healthy.

## What not to optimize for

Avoid these operating habits:

- counting PRs or agent actions as success without checking evidence;
- using Inbox as a generic log stream;
- letting automation pick work from vague backlog states;
- hiding secrets in prompts or config;
- adding broad prompts instead of tightening scope, knowledge, or policy;
- treating vendor-specific UI names as the Ship method.

## Where to next

Use [Troubleshooting](./troubleshooting.md) when something is broken, [Knowledge](./knowledge-buckets.md) when context is missing, [Configuration](./configuration.md) for repo wiring, and the [CLI reference](./configuration.md#local-commands) for command syntax.
