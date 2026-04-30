# E08 — Invite-only gating + waitlist

**Priority:** P2
**Effort:** S (~2 days)
**Owner:** TBD

## Goal

Public-facing landing has a "Request access" form. Submissions land in an admin queue. The maintainer approves a request → the user receives an email with a one-time invite token. Without a valid invite token, signup is rejected. The `plan` field stays `free` for the entire closed beta.

## Why

We do not want to operate Stripe in the closed beta. We do want capacity control, a real waitlist, a soft hand-curated cohort, and a way to prevent random Auth0 signups from sliding into Ship for free during beta.

The backend already has `routes/invites.py` with workspace-scoped admin invite minting and a public peek/accept endpoint. We extend that: add an "admin-grant" path for the platform-level "first access to Ship at all" invite, separate from "join my workspace" invites.

## Tasks

### T01 — Decision: where the gate lives **[S]**

- Two options:
  1. **Auth0 rule** — block sign-up at the Auth0 layer; a missing invite token in the application metadata = rejection. Cleaner, but couples to Auth0 features.
  2. **Backend gate** — accept the Auth0 callback, but on first JIT-provisioning (`POST /v1/auth/me`), check for a valid invite token attached to the email. Reject if missing.
- Pick (2) for simplicity and portability.
- Document decision in this file.

**Decision (2026-04-30):** Option B (backend gate at JIT-provisioning).
- Portable across IdPs (we may move off Auth0 someday)
- Self-hosted users keep the same gate logic without depending on Auth0 features
- Easier to test (just hit /v1/auth/me with a token from an unwhitelisted email)
- Audit log lives in our DB, not Auth0

**Implementation tasks:** see T02–T08 below.

**Acceptance:** decision recorded, ADR-style.

### T02 — Database: platform invites table **[S]**

- Migration: `0044_platform_invites.py` (or 0045 after E13).
- Table `platform_invites`: `id`, `email`, `token_hash`, `created_by_user_id`, `created_at`, `expires_at`, `accepted_at`, `accepted_by_user_id`, `note`.
- Token shown raw exactly once at issuance.

**Acceptance:** migration applies; pyright/sqlalchemy model works.

### T03 — Admin endpoints **[S]**

- Endpoints under `/v1/admin/invites` (admin-only by `is_platform_admin` flag on `users`).
  - `POST /v1/admin/invites` — issue invite for an email. Returns raw token + landing link.
  - `GET /v1/admin/invites?status=pending|accepted|all` — list.
  - `POST /v1/admin/invites/{id}/revoke` — invalidate.
- Audit log entry on every action.

**Acceptance:** REST collection works via cURL; Swagger documents it.

### T004 — JIT-provisioning gate **[S]**

- File: `backend/app/api/v1/routes/auth.py` (or wherever JIT happens) + `services/jit.py` (new if needed).
- On first login: look up platform invite by `email`. Reject if absent or expired or revoked. On accept, mark invite as accepted and bind to `user_id`.
- Already-provisioned users skip the check (returning users always allowed).

**Acceptance:** test cases:
- new email + no invite → 403 with "needs invite" message
- new email + valid invite → user created, invite marked accepted
- new email + revoked invite → 403
- existing user → 200

### T05 — Landing waitlist form **[S]**

- File: `landing/src/app/getting-started/page.tsx` (or a new section there).
- Form fields: email, role, current tracker, current agent. Free-text "what would Ship help with?".
- POST to `landing` API route (server action or API route) → forwards to a backend `/v1/admin/waitlist` endpoint that just stores it (a separate `waitlist_submissions` table).
- Confirmation: "Thanks. We review weekly."

**Acceptance:** submission lands in DB; the maintainer sees a row.

### T06 — Approval workflow **[S]**

- Maintainer's review:
  - Pull `waitlist_submissions` not yet linked to an invite.
  - For each: hit `POST /v1/admin/invites` with the email.
  - Email is sent (E09 dependency) with the invite link.
- For closed beta this can be a CLI command or a hand-curated process — a dedicated console page is post-beta.

**Acceptance:** documented procedure in `documentation/internal/operations/invite-runbook.md`.

### T07 — Invite landing page **[S]**

- File: new `landing/src/app/invite/[token]/page.tsx` (or in console at `console/src/app/invite/[token]/page.tsx` — already exists; verify).
- Visiting with a valid token → "You're invited. Sign in with Auth0 → workspace bootstrapped → you're in."
- Token expired/revoked → "This invite is no longer valid. Request again at /getting-started."

**Acceptance:** end-to-end invite flow tested with a real email.

### T08 — Capacity counter (soft cap) **[S]**

- Show a small public counter on the landing: "12 / 50 closed-beta seats taken". Readable from `/v1/admin/invites?status=accepted` count.
- When full: form switches to "Waitlist full. We'll reopen for the next cohort." Configurable cap in env.

**Acceptance:** counter renders; form disables at cap.

## Definition of done

- [ ] Random Auth0 signups without an invite are rejected.
- [ ] Waitlist form on landing works.
- [ ] Admin can mint and email invites.
- [ ] At least 5 hand-picked first-cohort users have made it through the flow.

## Risks / unknowns

- A determined user could share an invite link they received; tokens are single-use, but consider rate-limiting `accept` to the issued email only.
- Auth0 organizations could replace this entirely — defer that engineering decision until post-beta.

## Out of scope

- Self-serve referral / invite multiplier ("you're invited to invite 2 friends").
- Stripe metering / paid plans (`plan` stays `free`).
- Workspace-level invites (already exist via `routes/invites.py`).
- A pretty admin console for invites (CLI / direct DB ops are fine for closed beta).
