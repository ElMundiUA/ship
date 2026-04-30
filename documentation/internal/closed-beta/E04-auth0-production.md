# E04 — Auth0 production hardening

**Priority:** P0
**Effort:** S (~2–3 days)
**Owner:** TBD

## Goal

The console + backend run with `SHIP_AUTH_MODE=auth0` against a production Auth0 tenant. JWT validation, JIT user provisioning, claim mapping, and error UX are all correct. The local-mode email/password path stays alive but unadvertised.

## Why

The README claims dual-mode auth (local + Auth0). The console middleware delegates to the Auth0 SDK. The backend's `auth.py` uses `_ensure_local_mode` for local routes; the Auth0 path validates JWKS in `core/config.py` and dependencies in `api/v1/deps.py`. None of this has been audited end-to-end on prod.

A surprising number of public-beta failures will be auth: stuck signups, orphan sessions, missing email claims for SSO users.

## Tasks

### T01 — Production Auth0 tenant config audit **[S]**

- Verify `documentation/auth0-setup.md` matches current production tenant.
- Confirm:
  - **API audience** matches `AUTH0_AUDIENCE` env var on backend.
  - **Allowed Callback URLs** include `https://app.ship.elmundi.com/auth/callback`.
  - **Allowed Logout URLs** include `https://app.ship.elmundi.com`.
  - **CORS Origins** include the same.
  - **Refresh tokens** enabled with rotation.
  - **Email claim** present on access token (or use `userinfo`).

**Acceptance:** screenshots/links archived in `documentation/internal/auth0-prod-config.md` (gitignored secrets).

### T02 — Backend JWT validation hardening **[S]**

- File: `backend/app/api/v1/deps.py` and `backend/app/core/config.py`.
- Confirm:
  - JWKS URI cached with reasonable TTL.
  - `iss` and `aud` checked.
  - `exp` honored.
  - 401s return JSON `{ "detail": "..." }` not HTML.
  - 403 differs from 401 (token vs membership).
- Add a unit test for: expired token, wrong audience, missing claim, valid token.

**Acceptance:** `pytest backend/tests/test_auth*.py` covers the four cases.

### T03 — JIT user + workspace provisioning **[M]**

- First Auth0 login should:
  - Create a `users` row keyed on `auth0_sub` (the `sub` claim).
  - Map `email` from claim or fallback to a `userinfo` lookup.
  - Create the personal `orgs` + `workspaces` rows.
  - Add the user as `org_owner`.
- Idempotent on retries (no duplicate rows on race).

**Acceptance:** logging in twice from a new tenant lands on the same workspace; logging in a second user creates a separate org.

### T04 — Email-less SSO accounts edge case **[S]**

- Some Auth0 connections (e.g. enterprise SAML) do not provide email by default.
- Decision: require email for beta. If the claim is missing, render a "complete profile" page that asks for the email and saves it before continuing.

**Acceptance:** logging in with an email-less account produces the profile-completion page instead of a 500.

### T05 — Logout flow **[S]**

- File: `console/src/app/logout/route.ts`.
- Confirm Auth0 SLO (single logout) is invoked, cookie cleared, returns to landing.
- Test logging out from one tab invalidates the session in another tab on next request.

**Acceptance:** a logged-out tab redirects to landing on next API call.

### T06 — Error pages for auth failures **[S]**

- 401 in server component → redirect to `/login?next=...&reason=session_expired`.
- 403 → render `/no-access` with explainer.
- 500 from Auth0 callback → render `/auth-error` with retry + support link.

**Acceptance:** user never sees a stack trace; every auth failure produces a navigable error UI.

### T07 — Local-mode dimmed but alive **[S]**

- The local email+password flow stays functional for self-hosted users and for the maintainer's own bootstrap.
- Hide the local form behind `?mode=local` query param on `/login` so production users see Auth0 only.
- Add a one-line note in `documentation/auth0-setup.md`: "the local mode is intended for self-hosted instances".

**Acceptance:** default `/login` shows Auth0 button only; appending `?mode=local` reveals the form.

### T08 — Pre-launch checklist **[S]**

- Document the 8 things that must be true on the prod Auth0 tenant before each release.
- Live in `documentation/internal/auth0-checklist.md`.
- Reference from a CHANGELOG entry once first done.

**Acceptance:** checklist exists and was run before opening invites.

## Definition of done

- [ ] All 5 path-steps in E03 (`T02..T05`) pass against production Auth0.
- [ ] Auth-related test suite green.
- [ ] No 500 surfaced to users on auth-failure paths.
- [ ] Pre-launch checklist exists and was run once.

## Risks / unknowns

- Auth0 tenant rate limits during a launch surge — not a beta concern but worth noting.
- Bunny container startup may race the first JWKS fetch; cache miss handling needed.
- Email claim availability across providers is uneven; force-fix in T04.

## Out of scope

- Multi-tenant Auth0 (orgs as Auth0 organizations). Enterprise feature.
- SSO providers other than Auth0 (Okta, Azure AD direct).
- MFA enforcement policy.
- Session length tuning.
