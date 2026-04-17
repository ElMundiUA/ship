---
artifact_kind: collection
subkind: addendum
addendum_id: pharma
applies_to: [mobile-app, web-app, api-backend]
regulatory_frameworks: [HIPAA, GDPR, 21-CFR-Part-11, EU-AI-Act]
min_shipctl: "0.3.0"
---

# Addendum — Pharma / Health

## Scope

This addendum is a **domain overlay** for products that handle patient
health information or operate in a regulated pharma / health context.
It adds guardrails on top of a base preset (`web-app`, `api-backend`,
or `mobile-app`). It never replaces the base preset and never relaxes
a rule — it only tightens or annotates existing rules, per the
addendum rules in RFC-0004.

If a base preset enforces rule X, this addendum may add X' (a stricter
variant) or annotate X with an audit / retention requirement. It may
not remove X.

## PHI / PII handling rules

- **Never commit patient identifiers** (name, email, phone, MRN, dob,
  address, insurance id) to the repository, fixtures, tests, commit
  messages, or PR descriptions. This includes synthetic data that
  might be mistaken for real.
- **Log redaction** is mandatory. Application logs, crash reports,
  and error traces must filter known PHI / PII fields before leaving
  the process. Sentry (or equivalent) installations must configure a
  `beforeSend` hook that drops identifiers; see the Sentry
  integration notes in the Ship documentation for the baseline
  hook.
- **Structured error reports** must be de-identified by default.
  Tickets that attach an error dump must use fixtures or a
  sanitized extract, never the raw report.
- **Environment-variable convention:** use `LOG_DEIDENTIFY=1` (or
  equivalent) in every environment, including local dev. Code that
  bypasses redaction must be guarded by an explicit flag and
  reviewed.

## Audit log retention

- Application-level audit logs (who accessed which record, when,
  from where) MUST be retained for **at least 6 years** (HIPAA
  §164.530(j)). Longer if local law requires (e.g. certain EU
  member states and US states).
- Audit logs live in a **separate, immutable store** from operational
  logs. Object-lock (S3 Object Lock / GCS Bucket Lock) or an
  append-only log service is required; plain rotating files on a
  VM do not qualify.
- Access to the audit store is itself audited.

## Change management (21 CFR Part 11)

- 21 CFR Part 11 requires electronic records to be signed by the
  responsible individual for regulated records. At the repository
  level this translates to:
  - A `change-record` label on any ticket / PR that modifies a
    validated system, dataset, or calculation.
  - A **signed approval comment** on the tracker with a fixed
    format: `approved-by: <full name> <role> <timestamp-iso> <reason>`.
  - An identity trail — SSO with MFA on the tracker, GitHub, and the
    cloud console. Shared accounts are forbidden for anything
    touching production.
- Rejected changes require an equivalent `rejected-by: …` comment;
  re-submissions re-enter the same signature workflow.

## Access control overlay

- RBAC with a documented role list. At minimum: `dev`, `qa`,
  `release-manager`, `auditor`. Production deploy belongs to
  `release-manager`; `dev` may not deploy to production.
- Separation of duties: a PR author may not also be the sole
  approver or the deployer. CI enforces at least one non-author
  approval from a named code-owner before merge.
- Approval floor for production: a named `release-manager` must sign
  the promote gate. CI rejects production deploys initiated by
  anyone else.
- Break-glass procedure is documented separately (incident response
  playbook) and every break-glass use produces a post-incident
  review filed on the tracker.

## Data residency & subprocessors

- The trust document must list every subprocessor that touches PHI,
  each with a region (`eu-central-1`, `us-east-1`, …) and a BAA /
  DPA status.
- EU patient data stays in an EU region by default. A US-region
  subprocessor is only acceptable with a signed BAA and a documented
  Schrems-II transfer basis (SCCs, supplementary measures).
- Any new subprocessor goes through a recorded review; add a
  checklist item on the promote gate when a change introduces one.

## Dependency / supply chain

- An **SBOM** (CycloneDX or SPDX) is produced per build and stored
  with the release artifacts.
- SCA (dependency scan) runs in CI with **severity gating**: `high`
  or `critical` findings block the promote gate unless a documented
  exception is linked on the ticket.
- Production build artifacts are **signed** (Sigstore / cosign /
  equivalent). Deploy targets verify the signature; unsigned
  artifacts are refused.
- First-party CLI / SDK releases follow the same signing rule.

## E2E test overlay

- **Fixtures must be de-identified.** Test data is either fully
  synthetic or drawn from a vetted, de-identified dataset (with the
  de-identification method recorded in the fixture metadata).
- PHI must never appear in E2E recordings, screenshots, video
  captures, or flake debugging artifacts.
- Snapshot tests redact any field that might carry identifiers
  before comparison.
- A fixture-policy statement lives at the repo root (`FIXTURES.md`
  or equivalent) describing allowed data sources.

## Incident response addendum

- **Breach notification timelines** are hard constraints:
  - HIPAA: notify affected individuals within **60 days** of
    discovery (§164.404). Report to HHS as required by §164.408.
  - GDPR: notify the supervisory authority within **72 hours** of
    becoming aware of a personal-data breach (Art. 33).
- The incident-response playbook must include:
  - A breach-vs-incident classification step taken within the first
    24 hours.
  - A named communications owner for each applicable regulator.
  - A post-incident root-cause document filed on the tracker with
    the same signature workflow as change management.
- Post-incident, update the trust document and subprocessor list if
  the incident revealed gaps.

## What becomes mandatory in the SDLC

- The **security / architecture audit lane must exist as a separate
  lane** from the delivery lane. A single PR may not satisfy both
  lanes simultaneously.
- The audit lane is blocking for any PR carrying a `change-record`
  label. The delivery lane may not close until the audit lane signs
  off.
- Release notes must include a **"Changes that affect data
  handling"** section listing any modification to data flow,
  retention, access control, or subprocessor set. "None" is an
  acceptable value but the section itself is not optional.
- The promote gate for production must carry the signed
  `approved-by` comment; CI refuses the deploy without it.

## Interaction with the base preset

This addendum only **tightens** what the base preset already did:

- `web-app`: adds PHI redaction to the Sentry setup, audit log store
  behind Core-Web-Vitals telemetry, and the signed approval on any
  user-facing consent flow.
- `api-backend`: adds audit-log retention as an extra evidence type,
  SBOM + signed artifacts on the promote gate, and mandatory `BAA`
  review on any new subprocessor.
- `mobile-app`: adds PHI redaction to crash reporters, de-identified
  OTA payloads, and the signed approval on any store submission
  that changes data handling.

In no case does this addendum remove a rule the base preset
enforced; addendums can only add or annotate.
