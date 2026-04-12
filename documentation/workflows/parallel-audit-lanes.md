# Parallel audit lanes

**Intent:** tech / QA / security **audits** run on their own boards or projects so they never consume the **delivery Todo** queue.

## Invariants

- Audit findings link evidence (logs, scans, diffs); **no fabrication** rules when the signal is thin.
- Quiet mornings are a valid outcome—**silence is not failure** if the job truly found nothing to report.

## What you ship

- Separate Linear projects (or equivalent) and schedules distinct from SDLC.
- Prompt files for architect/security roles under `prompts/cloud-agent/`.

## Read next

- [Linear](/tools/linear) — multi-project hygiene.
- [Cloud agent — QA architect](/patterns/cloud-qa-architect) · [Security officer](/patterns/cloud-security-officer).
- [Examples → ElMundi](/docs/examples/elmundi) — daily audits chapter.
