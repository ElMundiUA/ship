# Agent launch matrix — one playbook, many agent surfaces

Default recommendation: **copy-paste prompt block** from [Getting started](../getting-started/index.md) into your preferred agent.

## Canonical files

| File | Purpose |
|------|---------|
| `prompts/onboarding/adopt-ship-generic.md` | Universal adoption workflow |
| `prompts/onboarding/adopt-ship-elmundi.md` | ElMundi-specific delta |
| `documentation/adoption/agent-setup-contract.md` | Interactive discovery contract |

## Cursor (IDE)

1. Open target repo.
2. Attach `@tools/ship/prompts/onboarding/adopt-ship-generic.md` (or equivalent path).
3. Add instruction: "Run this playbook end-to-end, ask discovery questions first, then open PR."

## Codex / CLI agents

```bash
codex -- "Read tools/ship/prompts/onboarding/adopt-ship-generic.md and execute it in this repo. Ask discovery questions before changing files."
```

## Claude Code

```bash
claude "Read tools/ship/prompts/onboarding/adopt-ship-generic.md and execute it in this repo. Start with interactive discovery questions."
```

## Copilot / chat surfaces

Paste the content of `adopt-ship-generic.md` + the short instruction above.

## Optional helper launcher

You can still use:

```bash
curl -fsSL https://raw.githubusercontent.com/ElMundiUA/ship/main/adopt-ship.sh | bash
```

Treat launcher as convenience, not the canonical contract.

## Required behavior across all agent surfaces

- Ask discovery questions before assumptions.
- Propose adaptation options when infrastructure is unknown.
- Record selected mapping (tracker/states/gates) in repo docs.
- Never commit secrets.
