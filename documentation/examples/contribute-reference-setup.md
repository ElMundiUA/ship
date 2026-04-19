# Contribute a reference setup

Ship evolves through real deployments. If your team is willing to share, contribute a reference setup.

## What to submit

1. Context:
   - company/team type,
   - stack shape (monorepo/multi-repo),
   - tracker + CI + agent runtime.
2. Implemented interfaces:
   - delivery lane,
   - QA + QA automation,
   - release gates,
   - daily digest/retro rhythm.
3. Evidence:
   - workflow snippets,
   - sample tracker mappings,
   - what failed first and how you fixed it.
4. Metrics (lightweight):
   - queue-to-PR time trend,
   - regression stability trend,
   - top recurring failure modes.

## Quality bar for inclusion

- Reproducible enough for another team to follow.
- Honest about trade-offs and constraints.
- No secrets, customer data, or sensitive internal URLs.
- Includes both successes and failure lessons.

## Privacy and security

Before opening PR:
- anonymize team/member names if needed,
- replace internal IDs and domains,
- remove any operational secrets and incident-sensitive details.

## Suggested file shape

Add under `documentation/examples/<your-reference>/`:
- `index.md` — architecture and flow,
- `operator-setup.md` — env/secrets and runbook,
- `workflows-catalog.md` — what runs when,
- optional local-language variant.

## Where to link it

Add a short entry in `documentation/examples/elmundi/index.md` (or the parent examples page when introduced) and link the new chapter from the docs index — the Next.js docs router under `landing/src/app/docs/[...slug]/page.tsx` picks it up automatically.
