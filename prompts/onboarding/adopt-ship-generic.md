# Ship — interactive adoption playbook (instruction-first)

You are a coding agent integrating Ship methodology into the current repository.

## Mandatory mode

Start with **interactive discovery**. Do not assume tracker, CI, deployment, or release policy.

Ask for or infer with confirmation:
1. Tracker system and current workflow states.
2. CI/scheduler system and trigger strategy.
3. Agent runtime preference (Cursor/Codex/Claude/etc.).
4. Quality gate expectations (manual QA, QA automation, regression policy).
5. Release policy (manual vs scheduled promote).
6. Daily digest/retro recipients (recommend DL aliases).

If uncertain, present 1-2 options and request a decision before implementation.

## Outcomes (definition of done)

1. Repository has a documented Ship adaptation plan (states, labels/fields, evidence policy).
2. `getting-started` style runbook exists in target repo with exact commands/entrypoints for that stack.
3. At least one automation path is wired (or explicitly documented as intentionally manual).
4. Daily digest + daily retro workflow policy is documented (email targets configured by user).
5. PR includes "Adoption notes" with:
   - chosen tracker mapping,
   - CI/scheduler mapping,
   - quality/release gates,
   - follow-up tasks.

## Guardrails

- Never commit secrets/tokens.
- Do not remove existing production workflows without approval.
- Keep changes minimal and auditable.
- Prefer vendor-neutral interfaces over vendor-specific jargon.

## Recommended artifacts to create/update in target repo

- `docs/ship-adaptation.md` (or equivalent)
- `.env.example` entries for chosen stack
- one workflow/runbook proving first green path
- concise rollback notes for automation changes

## If project uses legacy Ship runtime integration

Treat it as optional legacy. Migrate toward instruction-first docs and stack-native adapters unless user asks to keep runtime behavior.
