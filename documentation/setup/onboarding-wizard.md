# The onboarding wizard, end to end

The onboarding wizard is a four-step flow that connects your workspace to the repositories and infrastructure you want Ship to manage. Each step builds on the last, and the wizard auto-resumes from wherever you left off, so interruptions don't force a restart. Once complete, the wizard leaves behind a configuration scaffold—a `.ship/` directory in each activated repository, an initial set of inbox routing rules, and a knowledge seed read from your repository's CODEOWNERS file where one exists. This chapter walks you through one end-to-end session so you can run the wizard once and explain it to a colleague.

## Install GitHub App

The wizard begins by sending you to GitHub to install the Ship GitHub App on your organization or personal account. The App is what gives Ship its repository-level visibility: it reads commit history, listens to pull request events, and observes repository metadata. Without it, Ship cannot see into your repositories, so this step gates everything that follows.

When you click the button in the wizard, GitHub redirects you to the install page with Ship pre-selected. If you own the organization, you approve the install directly and Ship gains access to the repositories you specify in the next step. If you don't have permission to install on the organization—a common case in larger companies—GitHub forwards the request to an org admin instead, and the wizard tells you so. You can keep the wizard open; once an admin approves the request, refresh the page and proceed.

The App's exact scopes are listed on the GitHub install screen — typically read access to repository contents and metadata, the ability to comment on pull requests, read issues, and write the Actions secrets the workflows need. Ship does not push to default branches outside the workflows you ship; changes land as pull requests you review. If your organization requires additional review or attestation before installing third-party GitHub Apps, plan for that timeline — org admins often receive app install requests in a separate queue, and approval can take hours or days depending on your security processes.

Once the App is installed, the wizard moves to the next step. If you need to review the App's permissions, token scope, or refresh behavior later, see [GitHub App basics in the appendix](/docs/appendix/github-app).

## Pick repos

With the GitHub App installed, the wizard now shows you a list of repositories within the install scope—typically the organization or personal account where you installed the App. You choose which repositories Ship should activate.

Activation is a boundary. Only repositories you select here will receive PR comments, knowledge mirroring, or routine runs triggered by Ship. This is intentional: it lets you pilot Ship on a subset of your codebase before rolling out to everything. Check the boxes for the repositories you want to onboard now. You can add or remove repositories later from the workspace settings, so this choice is not final.

The list shows each repository's visibility (public or private), its language tags, and—if Ship has already analyzed it—its last sync time. If you have many repositories, the list is pageable and searchable by name. A common first move is to activate one or two high-traffic repositories where you have the most context, verify that the knowledge extraction and routing rules make sense, and then expand to others. Activating a repository does not change its permissions or configuration in GitHub; it only tells Ship to start observing it.

## Workspace tracker

Ship works best when it has a single source of truth for your work: a tracker that holds the tasks, epics, and roadmap. The wizard asks you to bind one tracker per workspace and gives you five options: Linear, Jira, GitHub Issues, GitLab, or Azure DevOps.

Your choice here determines where Ship looks for context when it encounters a mention in your code—a task ID in a commit message or a link in a pull request—and where it can create issues or post notes when routing rules require it. If you're not sure which tracker to use, fall back to GitHub Issues: it requires no additional credentials beyond the GitHub App you installed in step 1 and integrates seamlessly with the repositories you just activated.

Each tracker option has slightly different credential requirements. Linear, for instance, asks for an API token you can generate in your account settings. Jira asks for your instance site, email, and an API token. GitLab needs the host (if self-hosted), the group or namespace slug, and a personal access token. Azure DevOps asks for your organization name, project, and a PAT. GitHub Issues, as mentioned, uses the App and asks for nothing more. The full details for each tracker—field names, where to find credentials, token scope requirements—live in [Tracker binding](/docs/setup/tracker-binding); don't repeat that work here if you've already read it.

Choose the tracker that your team is already using. If you're in the early days and unsure, pick GitHub Issues and change it later: switching trackers is a workspace-level setting, not a per-repo operation.

## Confirm

The last step is a server-rendered preview of what activation will produce. The wizard shows you a draft `.ship/` configuration for each repository you activated, the default inbox routing ruleset that will filter incoming pull requests and commits, and a knowledge seed extracted from the CODEOWNERS file in each repository.

Reading this preview before pressing the final confirmation button is the cheap habit. You'll see the inferred routing rules — how Ship will sort an incoming Inbox item across owners and types — and, where a `CODEOWNERS` file exists in the repo, an initial set of ownership signals derived from it. This is your last chance to catch overly aggressive rules or misread `CODEOWNERS` entries before they activate. Questioning the preview now takes a few minutes; fixing it after it goes live often takes an hour of tracing why certain items landed with the wrong owner.

If something in the preview looks wrong—a routing rule that's too broad, or a knowledge seed that missed your most critical code paths—you can go back and adjust. Most of the time, however, the preview is close enough to start with. Inbox routing rules and the knowledge seed are both editable after activation, so imperfection is not a blocker.

## What's next

When you press the confirm button, the wizard shows you a Done screen. The activation is complete: your repositories are now under Ship's observation, your tracker is bound, and your workspace has a default routing ruleset and knowledge base.

From here, the natural next steps are to open your Inbox (where routed PRs await your review), visit the workspace home to get your bearings, and invite teammates so they can see the work you're routing. The knowledge seed will begin to grow as Ship encounters new patterns in your code. Don't expect it to be comprehensive on day one; it refines over time as more pull requests and commits flow through.
