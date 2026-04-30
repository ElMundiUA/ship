# E07 — Tracker bindings: Linear + GH Issues only

**Priority:** P1
**Effort:** S (~2–3 days)
**Owner:** TBD

## Goal

The two trackers we *advertise* in the closed beta are Linear and GitHub Issues. Both work bi-directionally (Ship reads, Ship comments, Ship updates state). All other tracker entries — Jira, Notion, Asana, ClickUp, Monday, spreadsheets — are hidden behind a feature flag with a "Coming soon" affordance.

## Why

The repo's `README.md` advertises a 14-tracker matrix; reality is **Linear: validated**, **GH Issues: partial**. Notion has OAuth wired but no real binding. Jira and the rest are aspirational.

For ElMundi (Track 5a) we need Linear solid. For Ship-on-Ship (Track 5b) and the .NET→Go project (Track 5c) we likely need GH Issues. Anything else makes the product look unfinished and breaks the "honest" posture from the front-door blog post.

## Tasks

### T01 — Linear bidirectional smoke **[S]**

- File: `backend/app/api/v1/routes/linear_oauth.py` + `backend/app/integrations/linear/`.
- Verify:
  - OAuth install (`/v1/integrations/linear/install/start` → callback) works on production Auth0 user.
  - Team + project picker stores `LinearWorkspaceConfig`.
  - Reading Linear issues for the bound team produces a usable list.
  - Posting a comment from Ship to a Linear issue lands and is visible.
  - Webhook from Linear (issue label change) updates Ship state within 30s.

**Acceptance:** end-to-end smoke test in `e2e/tests/tracker-linear.wired.spec.ts` passes.

### T02 — GitHub Issues bidirectional smoke **[S]**

- File: `backend/app/integrations/github/` + tracker_binding.
- Verify:
  - Bind a repo's issues namespace as the tracker.
  - Read open issues with the right labels.
  - Comment from Ship → comment on issue.
  - Webhook (issue labeled, commented, closed) updates Ship.

**Acceptance:** `e2e/tests/tracker-github-clarification.wired.spec.ts` passes; covers the round-trip.

### T03 — Hide partial trackers behind a flag **[S]**

- New backend setting: `enable_partial_trackers: bool = False` in `backend/app/core/config.py`.
- When `False`: tracker picker shows only Linear and GH Issues. Notion / Jira buttons render as disabled "Coming soon".
- When `True`: full picker (current behaviour).
- Flag readable from console via a `/v1/health/features` or `/v1/integrations/native/available` endpoint.

**Acceptance:** in production with the flag default-off, no UX path leads to a partially wired tracker.

### T04 — Update the catalog matrix in README **[S]**

- File: top-level `README.md`.
- Change the per-role tracker table:
  - Linear: validated
  - GitHub Issues: validated (was: partial)
  - Notion: planned (was: planned — keep)
  - Jira: hidden (was: partial)
  - others: planned
- Add a one-paragraph note: "Closed beta supports Linear and GitHub Issues only. Other trackers are behind a feature flag and will land post-beta."

**Acceptance:** README reflects reality; matches what the console picker offers.

### T05 — Tracker binding error UX **[S]**

- File: `console/src/app/r/[owner]/[repo]/settings/page.tsx` + `tracker_binding.py`.
- If the workspace lost its tracker connection (revoked OAuth, expired token), show a clear "Reconnect" CTA on every screen that depends on tracker data.
- Treat per-repo `tracker_binding` falling back to workspace default as healthy; treat workspace default missing as broken.

**Acceptance:** revoking the Linear token in Linear's UI causes the console to show a "Reconnect Linear" banner within one page-reload.

### T06 — Per-repo tracker binding default **[S]**

- A repo with no per-repo binding falls back to the workspace's default tracker.
- If both are missing, the repo is "tracker-not-bound" and runs that need the tracker should fail fast with a clear message.

**Acceptance:** `seed_default_pipelines` does not crash on a tracker-less workspace; instead it leaves the repo in a "needs tracker" state with an Inbox item.

### T07 — Webhook signature validation **[S]**

- Linear and GH Issues webhooks must verify signatures.
- Audit the existing handlers; add tests for forged-signature rejection.

**Acceptance:** unit test covers the verify-fail path.

## Definition of done

- [ ] Linear and GH Issues e2e wired tests green.
- [ ] Partial tracker UI hidden by default flag in production.
- [ ] README reflects the supported matrix.
- [ ] Reconnect UX exists for both trackers.

## Risks / unknowns

- Linear's webhook event shape may have changed since the integration was written; verify against the latest API.
- GH Issues "labels" semantics differ between repo and project (Projects v2 has different scoping).
- The `tracker_binding.py` route is fairly new; bugs here will cascade into E05 (adoption).

## Out of scope

- Building Notion / Jira integration. They stay hidden until post-beta.
- Slack / Teams as "trackers" — they're not.
- Cross-tracker work mirroring (Linear + Jira simultaneously).
- Spreadsheet-as-tracker beyond the existing `tracker-contract` artifact.
