# Bundle updates: the "Ship template update needed" banner

When you connect a repo to Ship, it installs a set of artifacts: agent rule files, generated workflow files, and any catalog-shipped patterns it depends on. This set is called a bundle. As Ship publishes updates, the current bundle version moves forward. When a connected repo's installed bundle falls behind, the workspace home shows a banner telling you an update is available. This chapter explains what the banner means, how to apply updates, and how to use pins to control rollout pace.

## What the banner means

The banner appears when the repo's `installed_bundle_version` is older than the workspace's `current_bundle_version`. It tells you which repos are behind and shows the version diff for each, for example `v3.4.1 → v3.5.0 available`. The banner is informational, not blocking. Your processes continue to run on the older bundle; there is no forced update at a particular time.

But drift has a shape: "behind by one minor version" tends to drift to "behind by three" if nobody applies. The banner is the prompt to keep that drift small. A repo six months behind the current bundle makes review harder and can create unexpected conflicts when you finally do update. Applying bundle updates within a week of publication keeps your team on stable, recent ground.

## Applying the update

Click the wizard button on the banner. The wizard walks you through the changes that will land: new or updated workflow files, refreshed agent rule blocks, any artefact pins that have moved. The wizard renders a diff and opens a pull request against the repo.

Reviewing the PR is the same process as reviewing any code change. There is no "auto-merge bundle updates" button on purpose. You should look at what changed — new rules, modified prompts, workflow syntax updates — and decide whether the diff makes sense for your team. If it does not, ask why before clicking merge. If you find a problem in the update itself, report it; don't just leave the banner unread.

## Pins for slowing rollouts

Some changes you want to opt into deliberately, not receive by default. Pins live in `.ship/config.yml` under `artifacts.pins`:

```yaml
artifacts:
  pins:
    pattern/role-developer: "1.4.2"
    collection/agent-rules-cursor: "^3.0.0"
```

A pin holds a specific version or a semver range for a named artefact. Pinning is the right tool when a routine prompt or a rule body must not drift during a sensitive rollout — a regulated release, a frozen sprint, a vendor migration. A pinned artefact will not advance until you remove the pin, even if a newer version is available in the bundle.

Treat pins as temporary. A pin you forget about is the same shape as a tech debt comment that stops being read. Review pins quarterly; if one is older than a sprint, ask yourself whether you still need it.

## When to skip a bundle update

You skip a bundle update when applying it would conflict with a release freeze, when the diff includes a change to a routine prompt that the team has not reviewed, or when the workspace owner has paused all bundle changes during an incident. Skipping is a decision; an unread banner is not. The banner stays visible until the update is applied or explicitly dismissed for that bundle version, so you will see it again tomorrow if you ignore it today.

Bundle updates are usually small, quiet, and worth applying within a week of publication. The expensive habit is letting the banner age until the diff is six months wide and nobody on the team remembers what changed. For context on where bundle artifacts live and which agents Ship supports, see the [`.ship/` folder chapter](/docs/developer/ship-folder) and the [roadmap](/roadmap).
