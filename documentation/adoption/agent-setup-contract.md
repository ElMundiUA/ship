# Agent setup contract (interactive)

Use this contract when an agent adapts Ship to a real project.

## Phase 1 — discovery interview (required)

The agent must ask and confirm:

1. **Tracker**: system, states, labels/fields, API limitations.
2. **Scheduler/CI**: what can run on cron/manual/webhook.
3. **Agent runtime**: Cursor/Codex/Claude/Copilot/custom.
4. **Quality model**: manual QA role, QA automation scope, regression cadence.
5. **Release policy**: manual or scheduled prod promote, required gates.
6. **Communication**: digest/retro email recipients (recommend DL aliases).
7. **Constraints**: compliance, data residency, no-go actions.

## Phase 2 — adaptation proposal

Agent provides 1-2 concrete implementation options with trade-offs:
- fast/simple option,
- stricter/governance-first option.

User chooses one before file edits.

## Phase 3 — implementation

Agent creates/updates:
- setup runbook,
- mapping docs (tracker states/labels equivalents),
- automation entrypoints,
- quality/release gates,
- daily digest + retro definitions.

## Phase 4 — validation

Agent must show:
- what was changed,
- what still requires human secrets/permissions,
- first green-path test plan,
- rollback path.

## Non-negotiable rules

- No secret commits.
- No destructive changes without explicit approval.
- No silent assumptions when infrastructure is unknown.
- Every automated transition must leave evidence.
