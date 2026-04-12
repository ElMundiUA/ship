# Tracker adaptation contract

This page replaces CLI-specific adapter docs with a vendor-neutral **adaptation contract**.

Ship does not require a fixed tracker tool. It requires a tracker surface that an agent can reason about and audit.

## 1) Minimum tracker interface

Your tracker (Linear, Jira, GitHub Issues, Azure Boards, ClickUp, spreadsheet, custom DB) should support:

- **Stable work item key** (human-readable, unique enough for PR/comments).
- **Queue state** (canonical name in Ship docs: `Todo`).
- **Execution states** (`In Progress`, `In Review`, `Done`, `Blocked`) or explicit mapping.
- **Machine-readable tags/labels/fields** for routing (`ready:*`, `stage:*`, `result:*`) or equivalents.
- **Comment/evidence trail** where agents and humans leave traceable decisions.

If your tool cannot provide one of these, the adoption agent must define a workaround explicitly.

## 2) Label/field semantics to preserve

Ship semantics (names can be mapped, meaning must stay):

| Semantic | Typical label/field |
|----------|---------------------|
| Ready for developer pickup | `ready:developer` |
| Current owner stage | `stage:*` |
| Failed / blocked outcome | `result:failed`, `result:blocked` |
| Human override required | `human:review-required` |

For tools without labels (e.g. spreadsheets), create equivalent columns and document mapping.

## 3) State mapping rules

Ship expects a queue-first flow:

`Backlog -> Todo -> In Progress -> In Review -> Done` (+ `Blocked` from anywhere)

If your tracker uses different names, define mapping in the project onboarding notes.

## 4) Evidence contract

Every automated transition should leave at least one of:

- tracker comment,
- CI run URL,
- PR URL,
- test report reference.

No silent state mutation without evidence.

## 5) Discovery questions the agent must ask

1. Which system is your source of truth for delivery queue?
2. What are your real state names today?
3. How do you represent labels/tags/custom fields?
4. Where does audit evidence live (comments, docs, Slack mirror)?
5. What is the fallback if the tracker API is unavailable?

## 6) Recommended output from adoption

The agent should produce a short `tracker-adaptation.md` in the target repo with:

- selected tracker system,
- state mapping table,
- label/field mapping table,
- evidence policy,
- known limitations.
