# Glossary

This glossary indexes every Ship-specific term you'll encounter in the manual. Unlike the [Vocabulary](/docs/orientation/vocabulary) chapter—which walks through core concepts step-by-step—this is a quick A–Z reference: one term, one sentence, one link to where it lives.

### Activation

The per-repo decision that lets Ship act on a connected repository (comment on PRs, mirror knowledge, fire routines). [GitHub App and repo activation](/docs/setup/github-app).

### Admin

Workspace role; can manage members and settings but cannot promote owners or delete the workspace. [Members and roles](/docs/setup/members-and-roles).

### Agent rule file

Versioned instructions installed at canonical paths each agent reads (CLAUDE.md, .cursor/rules/*.mdc, AGENTS.md, etc.). [Roadmap](/roadmap).

### API token

A workspace-minted credential for authenticating into Ship from CI or external tools. [Secrets](/docs/policies/secrets).

### Approval

Inbox item kind requiring explicit human sign-off before something irreversible. [Item types](/docs/inbox/item-types).

### Artefact

A versioned unit in the catalogue (pattern, tool, collection, preset). [Authoring](/docs/developer/authoring).

### Audit log

Admin-visible record of privileged mutations across the workspace. [Audit log](/docs/operating/audit-log).

### Bucket

Named container for related knowledge articles. [Buckets](/docs/knowledge/buckets).

### Bundle

The set of artefacts a repo currently has installed; the workspace home banner flags out-of-date bundles. [Bundle updates](/docs/developer/bundle-updates).

### Clarification

Inbox item kind: a question a routine needs answered before continuing. [Item types](/docs/inbox/item-types).

### Connected repo

A repository activated in the workspace. [Vocabulary](/docs/orientation/vocabulary).

### Disposition

The action that closes an Inbox item (answer, accept, approve, reject, retry, acknowledge, snooze, dismiss, reassign). [Disposition](/docs/inbox/disposition).

### Distiller

The path that routes raw imported content into a bucket and drafts an article for review. [Distiller and review](/docs/knowledge/distiller-and-review).

### Evidence

The trail tying an action to a tracker item, a PR, a CI run, a comment, or a knowledge article. [Evidence checklist](/docs/policies/evidence).

### Exception

Inbox item kind: a normal-rule override that needs an acknowledgement. [Item types](/docs/inbox/item-types).

### Failure

Inbox item kind: a configured action that broke and is waiting for a person. [Item types](/docs/inbox/item-types).

### Fence

The bounded-scope rule under which an agent acts (allowed paths, allowed actions, allowed states). [What Ship is](/docs/orientation/what-is-ship).

### Handle

Symbolic name (e.g., `secops`, `repo_maintainer`) that routing rules bind to a user, group, or strategy. [Routing rules](/docs/inbox/routing-rules).

### Improvement

Inbox item kind: a suggested enhancement to a routine or rule that needs accept / decline / defer. [Item types](/docs/inbox/item-types).

### Inbox

The workspace-wide attention surface for items needing decisions. [Inbox overview](/docs/inbox/overview).

### Knowledge

Product and repo context agents can use without guessing; lives in buckets. [Knowledge overview](/docs/knowledge/overview).

### Lane

Legacy term for `process`. [Process overview](/docs/process/overview).

### Member

Workspace role; reads everything and acts on routed items but cannot change settings. [Members and roles](/docs/setup/members-and-roles).

### Owner

Workspace role with full control, including the ability to delete the workspace. [Members and roles](/docs/setup/members-and-roles).

### Pattern

A versioned artefact in the catalogue describing a routine procedure. [Authoring](/docs/developer/authoring).

### Policy

Admin-authored standing rule injected into every agent's system prompt. [Policies](/docs/policies/policies).

### Process

The per-repo workflow definition (states, transitions, capacity, routines). [Process overview](/docs/process/overview).

### Roadmap

The page describing what Ship ships at production depth today and what we're growing toward. [Roadmap](/roadmap).

### Routine

A named job with a prompt and a default schedule that runs inside a process or standalone. [Routines](/docs/process/routines).

### Routing rules

The workspace-scoped binding from a handle to a target (user, group, or strategy). [Routing rules](/docs/inbox/routing-rules).

### Secret

A credential; lives in the workspace integration store, repo secrets, agent secrets, or API tokens—never in prompts or `.ship/config.yml`. [Secrets](/docs/policies/secrets).

### Specialist

Agent profile a routine takes on for a run (intake, business analyst, product manager, designer, technical architect, developer). [Tracker mapping and specialists](/docs/process/tracker-mapping-and-specialists).

### State

A column in the process workflow; intake, analysis, in progress, review, done, or whatever shape a team chooses. [Process overview](/docs/process/overview).

### Tracker

The system of record for product intent (Linear, Jira, GitHub Issues, GitLab, Azure DevOps). [Tracker binding](/docs/setup/tracker-binding).

### Tracker mapping

The panel in the process editor that binds tracker states to process states. [Tracker mapping and specialists](/docs/process/tracker-mapping-and-specialists).

### Workspace

The team or product area Ship operates inside. [Vocabulary](/docs/orientation/vocabulary).

### `.ship/`

The small folder Ship adds to a connected repo (config, lock, knowledge mirror, cache). [The .ship/ folder](/docs/developer/ship-folder).

### `shipctl`

The local CLI engineers use for setup, sync, validation, diagnostics. [shipctl](/docs/developer/shipctl).

---

For the long argument behind any term, [the book](/book) is where the why lives.
