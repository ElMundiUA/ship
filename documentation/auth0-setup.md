# Auth0 setup for the Ship cloud platform

This guide walks you through wiring an Auth0 tenant to a self-hosted Ship
deployment so the operator console authenticates end users via OIDC and the
backend validates the resulting access tokens against the tenant JWKS.

The flow is intentionally minimal — one Application + one API + the standard
"email" scope. There is **no** custom rule, action, or user-database
required for the basic setup.

---

## 1. Create the API (audience)

1. Auth0 Dashboard → **Applications → APIs → Create API**.
2. Name: `Ship`.
3. **Identifier** (this becomes `AUTH0_AUDIENCE`): `https://api.ship.local`
   for laptop dev, or your production URL (e.g. `https://api.ship.example`)
   for the live deployment. The string is opaque — Auth0 doesn't actually
   call it; it only has to match the backend config.
4. Signing Algorithm: **RS256** (default).
5. Save.
6. Optional but recommended — under **Settings → RBAC**:
   - Enable RBAC ✓
   - "Add Permissions in the Access Token" ✓
   That's what surfaces granted scopes in the JWT under the `permissions`
   claim. Ship doesn't require RBAC today, but the moment you want
   workspace-scoped tokens this is where you add `workspace:read`,
   `workspace:write`, etc.

## 2. Create the Console application

1. **Applications → Applications → Create Application**.
2. Name: `Ship Console`.
3. Type: **Regular Web Application**.
4. Click **Create**, then go to the **Settings** tab.
5. Note the **Domain**, **Client ID**, **Client Secret** — these become
   `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`.
6. Allowed Callback URLs (add one entry per environment):
   - `http://localhost:3001/auth/callback`  *(local dev)*
   - `https://ship.example.com/auth/callback`  *(prod)*
7. Allowed Logout URLs:
   - `http://localhost:3001`
   - `https://ship.example.com`
8. Allowed Web Origins (CORS for the SDK's session refresh):
   - `http://localhost:3001`
   - `https://ship.example.com`
9. Save.

## 3. Authorize the Console application against the API

1. **Applications → APIs → Ship → Machine-to-Machine Applications**.
2. Toggle **Ship Console** on.
3. (No permissions to grant yet — RBAC is optional.)

This step is what lets the Console request access tokens for the `Ship` API
audience. Without it, the SDK would silently fall back to ID tokens only.

## 4. Optional — enable email verification & passwordless

- Branding & UX live under **Branding → Universal Login** (logo, colors,
  custom domain).
- For the operator console we recommend keeping the **Database connection**
  on with email/password and adding **Google** as a social connection.
- "Force users to verify email" is under **Authentication → Database →
  Username-Password-Authentication → Settings**.

## 5. Wire credentials into your Ship deployment

```bash
./scripts/bootstrap.sh --auth0 \
    --domain     your-tenant.eu.auth0.com \
    --audience   https://api.ship.local \
    --client-id  <client-id-from-step-2> \
    --client-secret <client-secret-from-step-2>
```

Then, in `.env`, flip the auth mode and (for prod) point the SDK at the
public URL of the Console:

```bash
SHIP_AUTH_MODE=auth0
APP_BASE_URL=https://ship.example.com   # http://localhost:3001 for laptop
```

`make restart` and you're done. The `/login` page now shows a "Continue
with Auth0" button instead of the local email/password form.

**Local mode (email + password) is intended for self-hosted instances. In cloud (`SHIP_AUTH_MODE=auth0`) deployments, the form is hidden by default and accessible at `/login?mode=local` for emergency access only.**

## 6. Smoke-test the integration

```bash
make health
# Then in the browser:
#   http://localhost:3001/login
# Click "Continue with Auth0", complete the sign-in, you should land on /.
```

If something fails, the first thing to check is the backend log:

```bash
make logs-server | grep -E "(auth0|jwks|401)"
```

The most common errors:

| Symptom | Likely cause |
|---|---|
| 500 with "AUTH0_DOMAIN/AUTH0_AUDIENCE not configured" | Settings missing — re-run `bootstrap.sh --auth0 ...` then `make restart`. |
| 401 "signing key not found in JWKS" | `AUTH0_DOMAIN` typo, or the Console application is in a different tenant. |
| 401 "invalid access token: …audience" | `AUTH0_AUDIENCE` doesn't match the API identifier — they have to be byte-for-byte identical. |
| 401 "access token has no email claim" | Add an Auth0 Action that copies `event.user.email` into the access token; the `email` scope is OIDC-spec for ID tokens, not access tokens, so RS256-signed access tokens may omit it depending on tenant config. |

## 7. End-to-end testing with Mailosaur

For automated e2e against a *real* Auth0 tenant, point a [Mailosaur][mailo]
inbox at the test tenant's email connection so signup/verification can run
non-interactively. The test bot logs in via Auth0 → fetches the
verification email from Mailosaur → completes the flow → asserts a session
cookie comes back. We'll wire this into Playwright in Phase 2.

[mailo]: https://mailosaur.com/
