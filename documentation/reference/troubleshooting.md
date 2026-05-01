# Troubleshooting — symptom, cause, fix

When something stops working, the fastest path is to start with what you actually see, not what the system was supposed to do. This guide maps real symptoms to their causes and the quickest fixes.

The organizing principle: you'll navigate by your own experience (what you're looking at right now) rather than by the system's internal layers. If you see a banner, an empty dashboard, or a stuck routine, start in the matching section below.

## Console

### "I see the workspace home but no repos appear"

**Cause.** The GitHub App is installed but no repos were activated in the onboarding wizard, or the wizard completed without actually selecting any repos.

**Fix.** Open the onboarding wizard from the console banner, or jump to Settings → Repos and enable the toggle for at least one repository. Refresh the home once saved. See [onboarding wizard](/docs/setup/onboarding-wizard) for more detail on the full flow.

### "I logged in but I'm not in any workspace"

**Cause.** Your account exists but you have no active workspace membership; this happens when an admin revoked your access or your invite expired without being accepted.

**Fix.** Ask the workspace owner to re-invite you through the console Members interface. If you're sure you should have access, check your email for an Auth0 invite link that you may have missed. See [members and roles](/docs/setup/members-and-roles) if you're managing a workspace.

### "I'm redirected back to the onboarding wizard every time I log in"

**Cause.** The workspace has no activated repos, no shipped work history, and no dashboard data yet — the system assumes first-time setup.

**Fix.** Either activate at least one repo through Settings → Repos, or add `skipWizard=1` to the console URL as a temporary escape hatch while you're setting up. Once you've shipped something, you won't see the wizard again.

### "GitHub App install hangs on org admin approval"

**Cause.** Your GitHub organization requires admin approval for third-party applications to be installed; your install request is queued, waiting for an org admin to sign off.

**Fix.** Contact your GitHub organization admin (the same person who manages org-level settings and policies) to approve the Ship app install. Once approved, refresh the onboarding wizard in Ship and the repos should be available. See [GitHub App](/docs/setup/github-app) for org-level setup details.

## GitHub App and repos

### "Activated repo not showing PR comments or feedback from Ship"

**Cause.** Either the repo wasn't actually activated (it was in the install scope but unchecked in the wizard), or the bundled Ship config inside the repo is too old to support the features you're expecting.

**Fix.** Open Settings → Repos and confirm the activation toggle is on. If it is, check the workspace home for a "Ship template update needed" banner — click it and merge the update PR. This syncs your repo's `.ship/` config to the current format and usually resolves missing-feature symptoms.

### "GitHub App install did not return to Ship after I approved"

**Cause.** The installation callback was interrupted by a page navigation, the browser session timed out, or you selected the wrong GitHub account during the flow.

**Fix.** Start the GitHub install step from onboarding again while logged into both the intended GitHub account and this Ship workspace. The install should complete and show your activated repos in the workspace.

### "Repo shows 'template update needed' but I can't see an update PR"

**Cause.** The repo was activated but the update PR creation failed, usually because of branch permissions or a concurrent push to the target branch.

**Fix.** Check the repo's open PRs for any Ship automation PRs. If none exist, try opening Settings → Repos and clicking "Update" for that repo again. If it fails a second time, ensure the repo allows PRs to be created by automated accounts and that the default branch is not protected against bot pushes.

## Tracker binding

### "Work in progress is empty or missing tickets"

**Cause.** No tracker is bound to the repos, no repos are activated, or there genuinely are no open PRs or tickets in the configured tracker matching your team right now.

**Fix.** First, bind a tracker under Settings → Integrations and select the right project or team. Then confirm at least one repo is activated. Check your tracker directly to verify there are open items. Once both are in place, the workspace home should list the work. See [tracker binding](/docs/setup/tracker-binding) for the full setup.

### "A PR has no ticket key in its title"

**Cause.** The branch or PR name doesn't include the tracker identifier (like `PROJ-123`), or the tracker binding is missing or misconfigured so Ship can't resolve it.

**Fix.** Either link the PR to the ticket manually in the tracker UI, or rename the PR title to include the ticket key if your team uses that pattern. Then check Settings → Integrations to confirm the tracker binding exists and points to the right project. See [work visibility](/docs/operating/work-visibility) for how Ship maps PRs to tickets.

### "Tracker says authenticated but no tickets appear"

**Cause.** The binding is correct but the configured project, team, or group is empty, or everyone's tickets are marked as done.

**Fix.** Edit the tracker binding under Settings → Integrations and double-check the project key, group, or team name. Test by logging into your tracker directly and confirming there are open items in that project or group. If there are, run `shipctl doctor` from the repo CLI to check for auth drift.

### "Linear / Jira / Azure DevOps token expired"

**Cause.** The personal access token (PAT) or API token has reached its expiration date. Atlassian and Azure DevOps issue short-lived tokens by default.

**Fix.** Generate a fresh token on the tracker provider's settings page, then update it in Ship's Settings → Integrations under the tracker binding. Revoke the old token on the provider side so it doesn't linger. Don't just save a rotated token in Ship without invalidating the old one on the provider.

## Knowledge

### "Imported documents or repo files are stuck in 'pending review'"

**Cause.** The distiller routed them into a bucket but no one has reviewed and published them yet. Drafts are invisible to Ship's agents until published.

**Fix.** Open the Knowledge section in console, filter by bucket, and review the pending drafts. Read each one and click "Publish" to make it available. See [distiller and review](/docs/knowledge/distiller-and-review) for the full review workflow.

### "Repo `.ship/knowledge/*.md` edits aren't showing up in the workspace"

**Cause.** The mirror syncs on a schedule and may not have run since your merge, or the file path is outside the expected `.ship/knowledge/` root directory.

**Fix.** Verify the file is saved as `.ship/knowledge/something.md` in the repo. Run `shipctl sync` from the repo CLI to force an immediate sync if you suspect the schedule is stale. See [local knowledge](/docs/knowledge/local-knowledge) for how the mirror discovers files.

### "The assistant keeps giving stale product context"

**Cause.** Old or out-of-date knowledge is still published, or new facts were added to knowledge but not yet indexed by the system.

**Fix.** Search the Knowledge section for the stale article and update it with current information, or publish a new article that covers the correct fact. The assistant will cite the new version on the next run.

### "The same clarification question keeps appearing for every work item"

**Cause.** The answer exists as custom knowledge but isn't reusable in the right scope, or the automation is asking for a real decision that the system legitimately can't resolve without human input.

**Fix.** Add a short knowledge article with the answer so the automation can reference it without asking. Or, tighten the automation scope so it only asks when a real decision is actually needed. See [automations](/docs/operating/automations) for how to refine trigger conditions.

## Routines and the process

### "Routine ran but produced nothing"

**Cause.** Nothing was eligible (which is a valid answer and not a bug), or the eligibility query is too narrow, or a tracker mapping is wrong so no work matches the filter.

**Fix.** Open the audit log for that routine and scroll through the time series. Long stretches of empty runs with no activity are a sign something is wrong — the routine should at least have visibility into _why_ it ran and why nothing was eligible. Check the routine definition and the tracker state together. See [quiet systems](/docs/operating/quiet-systems) for a deeper look at when silence is expected.

### "Routine is in a failure loop with Inbox items"

**Cause.** Three consecutive failures have produced an Inbox failure item. The root cause is usually a missing credential, a vendor API outage, or a routine prompt that no longer matches reality.

**Fix.** Open the Inbox and read the failure payload — don't retry blindly. If it's credentials, update the secret store. If it's a vendor outage, wait and retry. If the prompt is stale, edit it and re-queue the work. See [disposition](/docs/inbox/disposition) for how to handle failure items.

### "The dashboard is quiet and I expected activity"

**Cause.** Either no eligible work exists right now (which is fine), the tracker binding is missing or misconfigured, or the automation trigger hasn't fired yet.

**Fix.** Confirm the tracker state and repo activation status. Check the audit log for the routine you expected to run. Don't widen the routine's eligibility just to make the dashboard look busy. See [the morning loop](/docs/operating/morning-loop) and [when the system quietly does nothing](/docs/operating/quiet-systems) for how to read absence honestly.

### "Evidence is missing for a decision"

**Cause.** The decision happened in Slack or chat outside the tracker, PR, or check trail, or the automation didn't write the result back where reviewers expect to see it.

**Fix.** Add the missing link or summary to the tracker item, PR, Inbox item, or knowledge article. Then update the workflow so future decisions write evidence back to the place reviewers actually check. See [evidence](/docs/inbox/evidence) for best practices.

## CLI and `.ship/`

### "`shipctl doctor` reports warnings"

**Cause.** Doctor surfaces drift early. Warnings can include: missing agent rule files, stale lock file, network unreachable, missing API key, or config schema errors.

**Fix.** Read the warnings in order and address each one. Most are one-line fixes: missing files can be synced with `shipctl sync`, network issues can be retested, and secrets can be re-added via `shipctl secret set`. See [shipctl](/docs/developer/shipctl) for the full CLI reference.

### "`shipctl verify` says rule files have drifted"

**Cause.** Someone edited an agent rule file inside the Ship-owned marker block, or the catalogue artifact bumped and your local copy hasn't synced yet.

**Fix.** Run `shipctl sync` to pull the latest catalogue. If drift persists after syncing, the edit needs to move outside the marker block — Ship-managed content belongs inside, custom notes belong outside. See [agent rules](/docs/developer/agent-rules) for the marker boundary conventions.

### "`.ship/` is not found"

**Cause.** You're running the command from a directory that doesn't have a `.ship/config.yml` file.

**Fix.** Change to the repo root with `cd`, or pass `--cwd /path/to/repo` to shipctl commands. Run `shipctl config show` to confirm you're in the right place and the config loads.

### "Agent rules were not installed"

**Cause.** The agent ID in your config is wrong, the artifact was not synced, or the target file already has a conflicting marker block.

**Fix.** Run `shipctl doctor` first to check for config issues. Then run `shipctl sync` to pull artifacts. Finally, try the install command again. Use `--force` only after confirming your custom edits are outside Ship-owned markers. See [agent rules](/docs/developer/agent-rules) for safe customization.

### "`shipctl verify` fails with multiple errors"

**Cause.** Config schema error, cache drift, missing rule target file, missing secret declaration, or API unreachable.

**Fix.** Read the first failing check, fix that one issue, and rerun `shipctl verify --check <that-one>`. Work through them one at a time. Use `--no-network` only when you're intentionally offline. See [verification](/docs/developer/shipctl#verification) for the full check list.

### "Sync reports a hash or cache mismatch"

**Cause.** A cached artifact was hand-edited or the downloaded content doesn't match the manifest.

**Fix.** Delete the affected entry from `.ship/cache/` and run `shipctl sync` again. Don't hand-edit cache files. Run `shipctl verify --check cache-integrity` to spot cache issues early.

## When in doubt

Start at the workspace home and work down: check the Inbox for error items, then the audit log for recent activity, then run `shipctl doctor` from the repo CLI. Most problems surface in one of those three places. Only jump to support after checking all three — you'll already have the context needed to describe the issue clearly.

This page grows as patterns of failure stabilize and repeat. The cheap habit is to add a new symptom→cause→fix block whenever you've debugged something the next person will hit too.
