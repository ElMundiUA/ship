# Binding the tracker

The tracker is your API schema. Between your organisation and processes, the issue tracker is not merely "where tickets live." It is the request surface: projects, states, labels, links to pull requests and CI — the fields that say what may move, what already moved, and what evidence exists. One workspace binds to one tracker. This is not a limitation. Every workspace has a single source of truth for priority, ownership, and closure: the tracker you choose here. State machines differ—Jira has traditional workflow states, Linear uses "archive" as a marker—so the binding step is intentional. Walk through each supported tracker below so you know what you are about to be asked for and why.

## Linear

Linear's API surface is clean and the state model is declarative. You authenticate with a Workspace API key from Linear settings (or via OAuth if your workspace uses it); Ship picks up the team list and workflow states at binding time, then uses them to validate routine decisions. You'll be asked which team and project to target by default—most routines will send requests to that project unless a workspace policy says otherwise. This path has low ceremony: no extra gates, no permission scopes to navigate, no token expiry to track.

## Jira

Jira sits behind Cloud and Data Center variants. You authenticate with four pieces: your Jira Cloud site (e.g., `yourorg.atlassian.net`), the email you authenticate with, an Atlassian API token, and a default project key (e.g., `ENG`). If your team runs self-hosted Data Center, you provide your host instead of a Cloud site; you may need a self-hosted gateway for network reach depending on your deployment. Start narrow: bind to one project, not the whole portfolio. Jira's permission scopes reward specificity, and a single project gives you faster feedback on what the tracker can do. See the [appendix entry for Jira API tokens](/docs/appendix/jira-token) if you need to generate one.

## GitHub Issues

GitHub Issues authentication is already handled by the GitHub App you installed in [the GitHub App chapter](/docs/setup/github-app). No additional credentials. Eligible issues come from the activated repos in your workspace—any issue across those repos can be a request destination. GitHub Issues is simpler than org-level project boards: you get repo-level views, labels, and assignees without the complexity of cross-repo Projects. If your team uses GitHub Projects heavily, expect a flatter, more repo-centric view than other trackers offer.

## GitLab

You provide a host (e.g., `gitlab.com` or your self-hosted host URL), a default group (e.g., `platform/core`), and a personal access token with project read/write scopes. Self-hosted GitLab works identically—the host field carries the URL. The group acts as a scope gate for the picker: you narrow the pool of eligible projects without locking into a single one. Start with a group to keep the eligible set human-sized, then target individual projects later if needed.

## Azure DevOps

Azure DevOps binds via organisation (e.g., `acme-corp`), default project (e.g., `Platform`), and a personal access token (PAT) with work item read/write scopes. One critical detail: PATs in Azure DevOps expire by default. Set a calendar reminder when you create the token so the binding doesn't quietly start failing in three months and leave requests stranded.

## Not on this list?

If your tracker is not here, open a request describing it and your use case. In the meantime, if your repositories already have GitHub Issues enabled, use that—it is the lowest-ceremony path and requires no new credentials. Notion can serve as a tracker via the integrations panel, but it is heavier to set up and is best treated as a knowledge import source unless your team truly uses Notion as your issue backlog.
