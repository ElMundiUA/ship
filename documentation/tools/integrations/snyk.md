# Snyk (security / dependency signal)

**Role in Ship:** optional **security/dependency signal** — findings feed evidence for risk decisions and PR hygiene; it is not a replacement for human review or QA gates.

## How to use it with agents

- Treat Snyk (or similar) output as **inputs** to tickets and PR comments, not silent auto-merge triggers unless policy explicitly allows.
- Keep **severity policy** in repo docs so agents do not invent CVSS drama where your org prefers calm triage.

## Read next

- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — where security evidence meets release gates.
- [Tools — capability map](/docs/tools) — “security/dependency signal” row.
