# GitHub App and repo activation

Connecting Ship to GitHub requires installing a GitHub App—not because we're being fussy about REST API semantics, but because the App model solves real operational problems that older approaches left unsolved. This chapter explains what the Ship App is, what it can see, how installation works, and why we keep repo activation separate from the install boundary.

## Why an App, not a token

The conventional way to give a bot access to GitHub is a personal access token: you generate one, hand it to the bot, and it authenticates using that token for every request. It's simple, fast to set up, and immediately familiar to anyone who has wired up a GitHub integration before. For a hobby project, it is fine. For a system that lives inside your development workflow and needs an audit trail, it creates problems that compound over time.

A token is a secret. Secrets get lost, rotated, revoked in anger, or left lying in log files. When a token disappears, you have no way to know whether the bot is still acting on your behalf—it just silently fails on the next request, and you hunt through error logs to understand why. The token carries a person's identity and permissions, which means revoking access requires finding the person, asking them to rotate it, or hoping you remembered where it was stored. If the token was created for a contractor who is no longer around, you have to guess. There is no straightforward way to audit which actions the bot performed, when, or whether it ever accessed something it should not have.

A GitHub App is a first-class citizen on your host. It has its own identity—a distinct name, icon, and URL on GitHub's installation screen. It has a formal permissions model: you grant it specific scopes, and GitHub enforces them at the API level, not at the app's discretion. It has webhooks, which means GitHub tells the App when things happen rather than the App constantly asking. Most importantly, it has audit logs: every action the App takes is attributed to "Ship," not to a person, and it shows up in your GitHub audit trail with a timestamp and a scope. If the App misbehaves, you revoke it in one step. No tokens to hunt down, no rotating of personal credentials, no wondering whether access is still active.

## The Ship App and its scope

When you install the Ship GitHub App, GitHub shows you a permissions screen. The App requests a small, defined set of scopes—enough for Ship to be useful, not more.

The Ship App can read the contents and metadata of repositories in the organizations or personal accounts where it is installed. It can read and write pull requests: this lets it comment on PRs as part of your processes, and it means the comments are clearly attributed to "Ship," not to a person's account. It can read issues, which is how Ship participates in issue tracking if you choose the GitHub Issues tracker. It can read organization members and team memberships, which is necessary for any workflow that mentions people or teams.

The App cannot push to your default branch or any other branch outside of the workflows you explicitly ship. It cannot read repositories outside the scope where it is installed—if you install it on your company org, it sees your company's repos but not your personal GitHub account. It cannot access your GitHub inbox, your notifications, your private repos in other organizations, or anything that belongs to another org entirely. Those boundaries are enforced by GitHub, not by our promise.

The exact list of scopes is shown on the GitHub installation screen when you authorize the App. We maintain this page for users who want to audit the permissions in detail, but we do not reproduce a frozen list here—GitHub's security model and our product needs change over time, and the authoritative source is always what GitHub itself displays when you install or update the App.

## Installing the App on your organization

The App can be installed at the organization level, which means it can act on any repository within that organization, or at the personal account level, which means it acts on your personal repositories. Installation flows through GitHub's standard OAuth screen.

If you are an organization admin, you can install the App directly. If you are not an admin, GitHub's OAuth flow will ask the organization admins to approve the installation. The Ship wizard will tell you this is happening and wait for approval—you cannot proceed until an admin grants permission. This is a security feature: it ensures that bots get a named administrator's sign-off before they get access to shared infrastructure.

Once an admin approves, the App is installed on your organization and can see all your repos. This is the install scope.

## Repo activation: a second boundary

Installing the App does not automatically activate every repository. Activation is where you draw the line between "repos the App can observe" and "repos the App is allowed to act on."

In the Ship workspace, you will see your organization's repositories listed under a Repos section. Every repo in the install scope appears there. But only repos you have activated are places where Ship will take action—comment on PRs, mirror the `.ship/knowledge` folder, fire scheduled routines, or anything else that touches GitHub. Repos you have not activated stay visible for reference and can be selected as part of a search or import, but they are inert. Ship will not push to them, will not comment on their PRs, and will not modify their issues.

This two-layer model—install scope and activation—gives you fine-grained control. You can install the App at the organization level and then activate only the repos where your team uses Ship. You can defer activating a repo while you work out whether Ship makes sense for your workflow. You can add new repos to the organization later, and they will not suddenly fall under Ship's jurisdiction; you activate them explicitly.

## What changes when you activate a repo

When you activate a repository, a few things happen.

First, you will see the `.ship/` configuration folder appear (often as a pull request for you to review). This folder holds Ship's rules for that repo: workflow definitions, trigger patterns, knowledge mirrors, and other configuration. You can edit these files as you would any other code—they are not hidden from you or locked by Ship.

Second, the workspace home will start showing health data for the repo: whether the last deployment succeeded, whether there are pending knowledge updates, whether any scheduled routines are failing or waiting. This gives you a dashboard view of Ship's activity across your repositories.

Third, any routines you define can now target this repo. A routine is a process Ship runs on a schedule—nightly, weekly, on a trigger—to do work like opening a maintenance PR or checking for stale issues. Routines do not run on repos that are not activated.

## How to revoke or change

Changes to the install scope—adding or removing repositories that Ship can see—go through GitHub's App settings page. You log into GitHub, visit the App's settings, and choose which organizations or personal accounts can use it. Revoking the install scope removes Ship's access to all repos in that account.

Changes to activation—turning a repo on or off for Ship processes—go through the Ship console. In the workspace settings or repos page, you can toggle repo activation. Deactivating a repo removes it from Ship's domain, but it does not uninstall the App.

This separation is deliberate. Losing access to one workspace or wanting to pause Ship on one repo should not require uninstalling the GitHub App and losing access everywhere else. Likewise, uninstalling the App should not leave orphaned activation state—the repos are simply inert. The two boundaries give you two ways to control the blast radius.

---

Once your repos are activated, Ship has the foundation it needs to act on your workflow: comment on PRs, mirror your knowledge, run routines. The App and activation are not the only security boundaries in Ship—your workflows themselves are explicit, versioned, and auditable—but they are the first ones, and the ones GitHub enforces on its own infrastructure. They are in place so that when something goes wrong, or when you want to stop, you have clear levers to pull and a clear audit trail of what happened.

For more detail on how authentication differs from other integration approaches, see [the appendix on GitHub Apps vs PATs vs OAuth](/docs/appendix/oauth-vs-api-key-vs-pat).
