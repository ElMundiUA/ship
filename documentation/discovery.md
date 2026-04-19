# Discovery contract

Normative spec for how an agent picks up a repo and brings it to a working
Ship installation. The contract has five phases (Phase 0 → Phase 4); the
agent moves to the next phase only when the previous one's deliverables
exist. Skipping a phase is a contract violation.

Audience: anyone integrating an agent (or a human) with Ship for the first
time on a given repo. Operators rerunning `shipctl` on a configured repo
do not need this page — see [Operating](/docs/operating).

## Phase 0 — machine-readable preamble (required)

Before opening the interview, the agent MUST:

1. **Look for `.ship/config.yml`.** If present, parse it (RFC-0002). Use
   `stack.tracker / stack.ci / stack.preset / stack.agents / stack.language`
   as defaults; surface them to the human for confirmation rather than
   re-asking blindly.
2. **Run `shipctl doctor --json`.** This calls every adapter's `detect(cwd)`
   hook (RFC-0004) and returns an inferred stack with confidence scores
   plus evidence. Offer those values as the default answers in Phase 1.
   Add `--write-inventory` if the human consents — `shipctl init --bootstrap`
   reads `.ship/inventory.json` to skip re-detection.
3. **Record every confirmed answer explicitly.** The agent keeps a structured
   block (yaml or json) with one key per Phase 1 question and writes it to
   the PR description so reviewers can audit which answers were inferred,
   confirmed, or overridden. The same block is the input to
   `shipctl init --bootstrap`.

If `.ship/config.yml` is absent and `shipctl doctor` is not yet installed,
fall straight into Phase 1 and run `shipctl init` at the end of Phase 3.

## Phase 1 — discovery interview (required)

The agent must ask and confirm:

1. **Tracker**: system (`linear|jira|github-issues|azure-boards|clickup|spreadsheet|none`), states, labels/fields, API limitations.
2. **Scheduler/CI**: what can run on cron/manual/webhook (`gh-actions|gitlab-ci|buildkite|circleci|azure-pipelines|jenkins|manual`).
3. **Agent runtime**: any subset of the 13 supported (Cursor, Codex, Claude/Claude-md, Copilot, Aider, Cline, Continue, Windsurf, Zed, Gemini, OpenCode, Cursor Cloud, Agents.md). Multiple are common.
4. **Quality model**: manual QA role, QA automation scope, regression cadence.
5. **Release policy**: manual or scheduled prod promote, required gates.
6. **Communication**: digest/retro email recipients (recommend DL aliases).
7. **Constraints**: compliance, data residency, no-go actions, addendums needed (`addendum-pharma`, `addendum-fin`, …).
8. **Preset**: choose one of `web-app|api-backend|mobile-app|cli|monorepo|adoption-minimum`.

Each answer overrides the corresponding default surfaced from Phase 0.

## Phase 2 — adaptation proposal

Agent provides 1-2 concrete implementation options with trade-offs:

- fast/simple option,
- stricter/governance-first option.

User chooses one before file edits. The chosen option is stored alongside
the Phase 1 record so the PR description shows both.

## Phase 3 — implementation

Before writing any file, the agent MUST follow the **artifact protocol**
(RFC-0001):

- Fetch `collection/agent-rules-<self>` for each agent the human picked
  (one per id) via `shipctl collection show <id>` or `shipctl collection
  fetch <id>`. The agent installs the body at the `install_target` declared
  in the artifact's front-matter, with the `marker` block, and a footer
  `<!-- ship-cli: installed-from collection/agent-rules-<id>@<version> -->`.
  This is exactly what `shipctl init --copy-rules` automates.
- Fetch `collection/preset-<preset>` for the chosen preset and apply its
  scaffolding (CI workflow, labels, secret list). For unsupported preset
  triples, `shipctl init --bootstrap` writes a `SHIP_BOOTSTRAP_PLAN.md`
  with the next-step checklist.
- Fetch any active addendum collections and apply them on top of the preset
  (addendums tighten or annotate; they never silently relax a base rule).

After installation the agent records every consumed artifact in the final
PR description as `<kind>:<id>@<version>`, one per line:

```
collection:agent-rules-cursor@1.0.0
collection:preset-web-app@2.1.3
pattern:cloud-developer@1.4.2
workflow:scheduled-sdlc-lane@2.1.0
```

Concrete deliverables for Phase 3:

- setup runbook,
- mapping docs (tracker states/labels equivalents),
- automation entrypoints (`.github/workflows/ship-pilot.yml` or equivalent),
- quality/release gates,
- daily digest + retro definitions,
- `.ship/config.yml` updated via `shipctl config set` (never edited by hand),
- `.env.example` extended via the `# --- ship-managed ---` block.

## Phase 4 — validation

Agent must show:

- what was changed (diff scoped per file),
- what still requires human secrets/permissions,
- first green-path test plan,
- rollback path,
- output of `shipctl verify` (and `shipctl verify --no-network` for offline
  reviews) — every check name + status,
- the consumed-artifact list with versions, mirrored in the PR description.

## Non-negotiable rules

- No secret commits.
- No destructive changes without explicit approval.
- No silent assumptions when infrastructure is unknown.
- Every automated transition must leave evidence.
- No copying of artifact bodies into the client repo — reference id + version.
- No mutation of `.ship/config.yml` outside `shipctl config` calls.
- Feedback is opt-in and human-initiated only (`shipctl feedback submit`).
