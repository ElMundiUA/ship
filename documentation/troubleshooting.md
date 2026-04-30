# Troubleshooting

Find the symptom, then check the likely cause, fix, and verification point. Start with product-facing symptoms; drop into CLI details only when repo wiring is the problem.

## Workspace and onboarding

### I am redirected back to onboarding

**Likely cause:** the selected workspace has no activated repos, no shipped history, and no meaningful dashboard data yet.

**Fix:** connect GitHub, activate at least one repo, or use the `skipWizard=1` escape hatch if you are intentionally inspecting an empty workspace.

**Verify:** the workspace home shows health, repos, work in progress, or shipped data instead of the onboarding step.

### A repo shows “template update needed”

**Likely cause:** the repo has no installed Ship bundle version or is behind the current bundle.

**Fix:** open the configuration wizard from the banner, review the generated changes, and merge the update PR.

**Verify:** the repo no longer appears in the update-needed banner.

### GitHub App install did not return to Ship

**Likely cause:** the installation callback was interrupted, the wrong account was selected, or the session expired.

**Fix:** restart the GitHub install step from onboarding while signed in to the intended GitHub account and Ship workspace.

**Verify:** activated repositories appear in the workspace and repo picker.

## Tracker and work visibility

### Work in progress is empty

**Likely cause:** no tracker is bound, no repo is activated, or there are no open PRs/tickets matching the current workspace.

**Fix:** bind the tracker for the repo or confirm that open PRs exist with recognizable ticket context.

**Verify:** the workspace home lists tracker-backed work or open PR fallback items.

### A PR has no ticket key

**Likely cause:** branch or PR naming does not include the tracker identifier, or the tracker binding is missing.

**Fix:** link the PR to the tracker item or rename/update the PR according to the team contract. Fix the tracker binding if Ship cannot resolve the source.

**Verify:** the work item shows a ticket reference instead of “No ticket key in title”.

### Inbox items go to the wrong owner

**Likely cause:** routing rules, groups, or repo ownership are stale.

**Fix:** reassign the current item with a reason, then update workspace routing so the next item resolves correctly.

**Verify:** the timeline records the reassignment and the next similar item lands with the intended owner.

## Knowledge

### The assistant gives stale product context

**Likely cause:** knowledge was not seeded, the repo mirror is stale, or an older article is still published.

**Fix:** update the relevant `.ship/knowledge/*.md` file or console knowledge article, then reindex or publish the new version.

**Verify:** the knowledge page shows the updated article and the assistant cites the new fact.

### The same clarification keeps appearing

**Likely cause:** the answer is not available as reusable knowledge or the automation scope is missing a required fact.

**Fix:** add a short knowledge article with the answer, or tighten the automation so it asks only when a real decision is needed.

**Verify:** new work no longer creates the same clarification.

## Automations and evidence

### An automation keeps failing

**Likely cause:** missing secret, missing knowledge, wrong trigger, unsupported repo shape, or a broken generated workflow.

**Fix:** inspect the latest evidence first. If it is a credentials issue, update the secret store. If it is context, update knowledge. If it is scope, disable or edit the automation before retrying.

**Verify:** the next execution leaves a successful check or a clearer Inbox item with one owner.

### The dashboard is quiet and I expected activity

**Likely cause:** no eligible work exists, the tracker binding is missing, or the automation trigger has not fired.

**Fix:** confirm the tracker state, repo activation, and recent workflow activity. Do not widen the automation just to make the dashboard look busy.

**Verify:** either the workspace remains quiet for a clear reason, or the missing binding/trigger is fixed and evidence appears.

### Evidence is missing for a decision

**Likely cause:** the decision happened in chat, outside the tracker/PR/check trail, or the automation did not write back.

**Fix:** add the missing link or summary to the tracker item, PR, Inbox item, or knowledge article. Then fix the workflow so future decisions write evidence where reviewers expect it.

**Verify:** a future reader can follow the decision without Slack archaeology.

## CLI and local setup

### `.ship/` is not found

**Likely cause:** you are not running the command from a repo with `.ship/config.yml`.

**Fix:** `cd` to the repo root, pass `--cwd`, or run setup from the console/developer setup path first.

**Verify:** `shipctl config show` prints the expected config.

### Agent rules were not installed

**Likely cause:** the selected agent id is wrong, the artifact was not synced, or the target file already has a conflicting marker.

**Fix:** run `shipctl doctor`, then `shipctl sync`, then reinstall the rules for the declared agents. Use `--force` only after confirming custom edits are outside Ship-owned markers.

**Verify:** `shipctl verify --check rules-markers,agents-on-disk` passes.

### `shipctl verify` fails

**Likely cause:** config schema error, cache drift, missing rule target, missing secret declaration, or unreachable API.

**Fix:** read the first failing check, fix that one issue, and rerun the specific check. Use `--no-network` only when you are intentionally offline.

**Verify:** `shipctl verify` exits `0`, or every remaining warning has an explicit reason in the PR.

### Sync reports a hash or cache mismatch

**Likely cause:** cached artifact content was edited or the downloaded body does not match the manifest.

**Fix:** delete the affected cache entry and run `shipctl sync` again. Do not hand-edit `.ship/cache/`.

**Verify:** `shipctl verify --check cache-integrity` passes.

## When to escalate

Fix in place when one clear change ends the failure class. Escalate when the same symptom repeats across repos, owners, or runs. Repeated failures usually mean a missing boundary, stale knowledge, bad routing, or an artifact that needs revision.

## Where to next

Use [Operating](./operating.md) for normal review, [Knowledge](./knowledge-buckets.md) for context fixes, [Configuration](./configuration.md) for repo wiring, and the [CLI reference](./configuration.md#local-commands) for command syntax.
