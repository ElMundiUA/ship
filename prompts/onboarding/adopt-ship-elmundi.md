# ElMundi addendum (for instruction-first adoption)

Apply after `adopt-ship-generic.md`.

Target context: ElMundi-style monorepo (`website/`, `.github/workflows/`, delivery + audit lanes).

## ElMundi-specific expectations

- Queue column canonical name: `Todo`.
- Delivery and audit lanes stay separate.
- QA verifies behavior first; QA automation encodes it into reusable tests.
- Daily digest and daily retro emails are required (DL recommended).

## Migration guidance

- Historical names like `linear-agent-*.yml` may remain in repo history; normalize to neutral naming where feasible.
- Preserve one canonical branch naming rule per ticket to avoid duplicate PRs.
- Keep ElMundi as a reference implementation page in docs and update it when process changes.

## Verification targets

1. Morning digest definition present.
2. End-of-day retro recommendations definition present.
3. Weekly (or policy-defined) prod promotion gate linked to regression evidence.
4. Human merge ownership remains explicit.
