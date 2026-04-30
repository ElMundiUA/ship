# Auth0 Production Tenant Configuration Audit

This document captures the production Auth0 tenant configuration values for Ship. It serves as a values audit distinct from the process checklist (`auth0-checklist.md`). Fill in the "Current value" column manually during your audit; do not commit actual values to version control.

---

## Required tenant settings

Verify the Auth0 Dashboard matches the expected values below:

| Setting | Expected value | Current value | Verified (Y/N) | Notes |
|---------|----------------|---------------|----------------|-------|
| **API Audience** | `https://api.ship.elmundi.com` (or your prod URL) | TBD | | Auth0 Dashboard → Applications → APIs → Ship → Identifier. Must match `AUTH0_AUDIENCE` backend env var byte-for-byte. |
| **Allowed Callback URLs** | `https://app.ship.elmundi.com/auth/callback` | TBD | | Auth0 Dashboard → Applications → Applications → Ship Console → Settings. Exact redirect target after OIDC login. |
| **Allowed Logout URLs** | `https://app.ship.elmundi.com` | TBD | | Same settings page. SLO redirect target. |
| **Allowed Web Origins (CORS)** | `https://app.ship.elmundi.com` | TBD | | Same settings page. Permits the SDK's session refresh from the browser. |
| **Refresh Token Rotation** | Enabled | TBD | | Auth0 Dashboard → Applications → APIs → Ship → Settings → Refresh Token Rotation. Toggle: **On**. Expiration interval: 7 days (default acceptable). |
| **Email claim on access token** | Present (via custom Action or Auth0 rule) | TBD | | Default Auth0 behavior omits `email` from access tokens targeting a custom API audience. If absent, create an Action under Manage → Actions → Flows → Login: `context.accessToken.email = user.email`. |
| **JWKS endpoint reachable** | `https://{AUTH0_DOMAIN}/.well-known/jwks.json` responds with keys | TBD | | Backend must reach this URL to validate token signatures. Contains a `keys` array with signing key objects. |
| **Token expiry** | Typically 24–36 hours (Auth0 default 86400 seconds = 24h) | TBD | | Auth0 Dashboard → Applications → APIs → Ship → Settings → Token Expiration (For Browser). Short-lived tokens minimize the impact of credential leaks. |

---

## Backend environment variables

The backend (`backend/app/core/config.py` and `backend/app/security/auth0.py`) reads these Auth0-related variables:

| Variable | Description | Expected production format | Mandatory |
|----------|-------------|----------------------------|-----------|
| **SHIP_AUTH_MODE** | Authentication mode selector. | `auth0` (production) or `local` (dev/self-hosted) | Yes |
| **AUTH0_DOMAIN** | Auth0 tenant domain. | `your-tenant.eu.auth0.com` or custom domain | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_AUDIENCE** | API identifier (opaque string used as `aud` claim in JWTs). | `https://api.ship.elmundi.com` | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_ISSUER** | Token issuer override (defaults to `https://{AUTH0_DOMAIN}/`). Custom domains may emit different `iss`. | `https://your-tenant.eu.auth0.com/` | No (optional) |
| **AUTH0_JWKS_URL** | JWKS endpoint override (defaults to `https://{AUTH0_DOMAIN}/.well-known/jwks.json`). | `https://your-tenant.eu.auth0.com/.well-known/jwks.json` | No (optional, tests only) |
| **SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS** | Allow localhost URLs in Auth0 callback origins (laptop dev only). | `false` (production) or `true` (local dev with shared tenant) | No (dev only) |

### Notes:
- `AUTH0_DOMAIN` and `AUTH0_AUDIENCE` are mandatory when `SHIP_AUTH_MODE=auth0`. The backend refuses to boot without them (500 error with a helpful message).
- `AUTH0_ISSUER` and `AUTH0_JWKS_URL` are optional overrides useful for custom Auth0 domains or testing. Leave unset unless you have a specific reason (custom tenant domain, local JWKS stub for tests).
- `SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS` permits localhost callback URLs; production deployments must set this to `false` (default). Only local dev with a shared tenant should enable it.
- The backend caches JWKS keys in-process with a 1-hour TTL; key rotation is automatic (cache miss triggers a refresh).

---

## Console environment variables

The console (`console/src/lib/auth0.ts`) reads these Auth0-related variables:

| Variable | Description | Expected production format | Mandatory |
|----------|-------------|----------------------------|-----------|
| **SHIP_AUTH_MODE** | Authentication mode selector (same as backend). | `auth0` | Yes |
| **AUTH0_DOMAIN** | Auth0 tenant domain (same as backend). | `your-tenant.eu.auth0.com` | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_CLIENT_ID** | Console application client ID. | UUID-like string from Auth0 dashboard | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_CLIENT_SECRET** | Console application client secret. | Long random string from Auth0 dashboard | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_AUDIENCE** | API identifier (same as backend). | `https://api.ship.elmundi.com` | Yes (if SHIP_AUTH_MODE=auth0) |
| **AUTH0_SESSION_SECRET** | Session encryption key for the SDK's HTTP-only cookie. | 32-byte hex string (e.g., `0123456789abcdef0123456789abcdef`) | Yes (if SHIP_AUTH_MODE=auth0) |
| **APP_BASE_URL** | Browser-facing console origin (callback redirect target). | `https://app.ship.elmundi.com` | Yes (if SHIP_AUTH_MODE=auth0) |

### Notes:
- `AUTH0_CLIENT_ID` and `AUTH0_CLIENT_SECRET` identify the **Console application** (Regular Web Application type in Auth0 dashboard). Do not confuse with backend credentials.
- `AUTH0_SESSION_SECRET` encrypts the session cookie; generate it with `openssl rand -hex 16` (32 hex chars = 16 bytes).
- The console SDK (`@auth0/nextjs-auth0/server`) mounts auth routes automatically: `/auth/login`, `/auth/callback`, `/auth/logout`, `/auth/profile`, `/auth/access-token`.
- Scopes: the SDK requests `openid profile email offline_access` (hardcoded in the init; allows refresh tokens for long-lived sessions).

---

## Verification commands

Use these shell commands to audit the production deployment:

```bash
# 1. Verify JWKS endpoint is reachable and has keys
curl -s "https://${AUTH0_DOMAIN}/.well-known/jwks.json" | jq '.keys | length'
# Expected output: a number > 0 (e.g., 2)

# 2. Check that backend env vars are set
echo "Backend Auth0 env vars:"
echo "SHIP_AUTH_MODE=${SHIP_AUTH_MODE}"
echo "AUTH0_DOMAIN=${AUTH0_DOMAIN}"
echo "AUTH0_AUDIENCE=${AUTH0_AUDIENCE}"
echo "AUTH0_ISSUER=${AUTH0_ISSUER:-<unset>}"
echo "AUTH0_JWKS_URL=${AUTH0_JWKS_URL:-<unset>}"

# 3. Check that console env vars are set (run on console pod/container)
echo "Console Auth0 env vars:"
echo "SHIP_AUTH_MODE=${SHIP_AUTH_MODE}"
echo "AUTH0_DOMAIN=${AUTH0_DOMAIN}"
echo "AUTH0_CLIENT_ID=${AUTH0_CLIENT_ID}"
echo "AUTH0_AUDIENCE=${AUTH0_AUDIENCE}"
echo "APP_BASE_URL=${APP_BASE_URL}"
# Do NOT echo AUTH0_CLIENT_SECRET or AUTH0_SESSION_SECRET

# 4. Inspect a fresh access token at https://jwt.io after logging in
#    Verify:
#    - Header: "alg": "RS256"
#    - Payload: "aud": "<AUTH0_AUDIENCE>", "iss": "<AUTH0_ISSUER>", "email": "<user@domain>"
#    - Expiration: "exp" is a future Unix timestamp

# 5. Trace backend logs for Auth0 validation
make logs-server | grep -E "(auth0|jwks|401)" | tail -20

# 6. Verify SLO (single logout) by logging out and checking the JWKS cache is cleared
#    Log in → open browser console → click logout → should redirect to landing
```

---

## Note on secrets

**🔒 Do not commit actual values to this file.** This is a template for the maintainer to fill in locally.

When auditing the production tenant:

1. **Create a local copy** (e.g., `auth0-prod-config.local.md` in your home directory or a secure notes app).
2. **Fill in the "Current value" columns** with values from the Auth0 Dashboard.
3. **Keep the local copy out of git** — add `.local.md` to your personal `.gitignore` or use a password manager.
4. **Verify against the expected values**, then check the "Verified" column.
5. **Delete the local copy** after completing the audit (or keep it in a password manager for future audits).

Environment variables like `AUTH0_CLIENT_SECRET` and `AUTH0_SESSION_SECRET` must **never** be committed to version control. They live in `.env` files (which are `.gitignore`d) and secret management systems (Fly.io Secrets, Kubernetes Secrets, etc.).

---

## References

- [Auth0 setup guide](../auth0-setup.md) — how to create and wire the Auth0 tenant.
- [Auth0 pre-launch checklist](./auth0-checklist.md) — process checklist to verify before each release.
- [E04 Auth0 production hardening](./closed-beta/E04-auth0-production.md) — full epic scope.
