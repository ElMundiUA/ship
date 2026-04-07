# Security & privacy brief

**Purpose:** high-level **data-flow and trust** narrative for stakeholders (not step-by-step secret setup).  
**Audience:** security officers, architects, procurement.  
**Outcomes:** clear questions answered at the right altitude; pointers to vendor documentation.

## Components

- **GitHub** — stores code, runs workflows, holds **Actions secrets** (e.g. `LINEAR_API_KEY`, `CURSOR_API_KEY`).
- **Linear** — issue state, comments, projects; updated via API when keys are present.
- **Cursor Cloud Agent** — runs in Cursor’s cloud against a **clone** of the repository when invoked by the orchestrator.
- **Optional:** Snyk (dependency findings), email (SendGrid), hosting CDNs — as configured in your deployment.

## Secrets & duplication

- GitHub supplies secrets to workflows. For agents to **update Linear**, the same Linear credential may need to exist in **Cursor’s Cloud Agent environment** for the repository (see [Cursor Cloud secrets](CLOUD-AGENT-SECRETS.md)).
- **Procurement question to validate with your org:** which systems classify as **subprocessors**, and whether duplicate secret placement is acceptable under your policy. This doc does not replace a DPIA or vendor DPA.

## Data flows (conceptual)

1. **Workflow trigger** → checkout → pick/issue metadata from Linear (API).
2. **Agent launch** → prompt + repo context to Cursor API → branch/PR workflow.
3. **Audits** → may attach **Snyk JSON** or repo analysis outputs; tickets created only with evidence ([Daily audits](DAILY-AUDIT-ROLES.md)).

## Risk notes (plain language)

- **Credential exposure** — follow least privilege; rotate keys; scope secrets to repository or environment.
- **Third-party processing** — code and prompts may be processed by the agent provider under **their** terms; review Cursor, GitHub, and Linear policies for your regime.
- **Prompt injection** — prompts should treat issue text as untrusted input; operational mitigations live in prompt design and review.

## Official references (external)

- [Cursor — Cloud Agents API](https://cursor.com/docs/background-agent/api/overview)
- [GitHub — Encrypted secrets](https://docs.github.com/en/actions/security-guides/encrypted-secrets)
- [Linear — API](https://developers.linear.app/)

## Operator detail (internal)

- Secret placement and env vars: [Cursor Cloud secrets](CLOUD-AGENT-SECRETS.md) · [Autonomous pipeline setup](AUTONOMOUS-SETUP.md).
