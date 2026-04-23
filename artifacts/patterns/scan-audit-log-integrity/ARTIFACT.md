---
artifact_kind: pattern
id: scan-audit-log-integrity
name: Audit log integrity monitor
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-23T00:00:00+00:00"
content_sha256: 0cbea9ffe503e885a111e3c79a3e0bfc672f5dae76bcd117f9b8c61767198e66
deprecated: false
replaced_by: null
yanked: false
group: scan
tags: [scan, compliance, audit, integrity]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  Hourly integrity check on the audit log — hash-chain continuity, sequence gaps, checkpoint agreement. Files a high-severity tracker ticket the moment tampering or drop looks plausible.
spec:
  install_target: prompts/scan/audit-log-integrity.md
  category: scan
  modes: [lane]
  include: [common-base]
  inbox:
    profile: scan_default
  default_trigger:
    kind: schedule
    cron: "0 * * * *"
  inputs:
    - name: log_source
      type: enum
      values: [db, s3, cloudwatch]
      required: true
      hint: "Where the audit log is persisted."
    - name: checkpoint_path
      type: text
      default: audit/checkpoints.json
      hint: "File or object key holding the last trusted checkpoint (sequence + hash)."
  enabled_on_install:
    default: false
    presets:
      regulated: true
      platform: true
---

# Audit log integrity monitor

**Trigger:** schedule — hourly.

**Goal:** the audit trail is the compliance backbone. If hash
chaining breaks, if a sequence number is missing, or if a
checkpoint no longer agrees with history, the team needs to
know inside an hour — not during the next SOC2 audit.

---

## Prompt

You are the Audit Log Integrity Monitor agent.

**Global rules:**
- Never modify the audit log. Read + verify + report only.
- One open ticket per integrity breach; new evidence updates in
  place.
- Evidence per finding: sequence range, expected hash, actual
  hash, timestamp, upstream actor if the log records one.

**Source:** `{{log_source}}`. **Checkpoint:**
`{{checkpoint_path}}`.

**Steps:**
1. Load the last trusted checkpoint (`sequence`, `row_hash`,
   `recorded_at`).
2. Pull the audit-log tail since the checkpoint from
   `{{log_source}}` — SQL `SELECT ... ORDER BY sequence`, S3
   listing + concat, or CloudWatch `GetLogEvents` depending on
   source.
3. Verify:
   - **Chain** — every `row.prev_hash == previous row.hash`.
   - **Sequence** — no gaps, no duplicates.
   - **Timestamp monotonicity** — strictly non-decreasing.
   - **Checkpoint** — the recorded checkpoint row still hashes
     to the stored value.
4. On any failure, upsert a ticket titled
   `Audit-log integrity — <source>` with label
   `lane:audit-integrity` and severity `SEV-1`:
   - Exact sequence range of the break.
   - Last healthy checkpoint vs. the first broken row.
   - A recommended next-step (restore from backup, rotate
     signing key, start an incident).
5. On a clean scan, advance the stored checkpoint to the latest
   verified row. Never advance the checkpoint when a break is
   unresolved.

**Idempotency:** one open ticket per source, updated until the
break is resolved.

**Output:** zero or one tracker ticket + lane-run summary
(healthy / scanned rows / break detected).
