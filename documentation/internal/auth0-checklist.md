# Auth0 Production Pre-Launch Checklist

Before each release and after environment variable rotation, verify the 8 items below on the production Auth0 tenant. This checklist ensures JWT validation, claim mapping, and user provisioning work correctly across logins and API calls.

**Reference:** [Auth0 setup guide](../auth0-setup.md)

---

## Checklist

1. **API audience matches backend config**
   - Auth0 Dashboard → Applications → APIs → Ship → Settings
   - Verify **Identifier** equals `AUTH0_AUDIENCE` in `.env` (e.g. `https://api.ship.elmundi.com`)
   - Command: `echo $AUTH0_AUDIENCE` and cross-check manually

2. **Callback URLs include production domain**
   - Auth0 Dashboard → Applications → Applications → Ship Console → Settings
   - Verify **Allowed Callback URLs** includes `https://app.ship.elmundi.com/auth/callback`
   - Add entry if missing and redeploy backend with updated tenant URL

3. **Logout URLs and CORS origins configured**
   - Same application settings page
   - **Allowed Logout URLs** includes `https://app.ship.elmundi.com`
   - **Allowed Web Origins** includes `https://app.ship.elmundi.com`
   - These prevent "redirect mismatch" and SLO (single logout) failures

4. **Refresh token rotation enabled**
   - Auth0 Dashboard → Applications → APIs → Ship → Settings → Refresh Token Rotation
   - Verify **Rotation is enabled** (toggle on)
   - Verify **Rotation expiration** is set to a reasonable interval (default 7 days is acceptable)

5. **Email claim present on access token**
   - Auth0 Dashboard → Manage → Actions → Flows → Login
   - Check for an action that adds `email` to access token (not just ID token)
   - If absent, create a custom action: `context.accessToken.email = user.email`
   - Verify by inspecting a fresh login token at [jwt.io](https://jwt.io) for `email` field

6. **JWKS URI reachable from backend**
   - Backend pod: `curl -s "https://<domain>/.well-known/jwks.json" | jq '.keys | length'`
   - Should return a number > 0 (number of signing keys)
   - If 0 or connection refused, check tenant domain and network firewall

7. **SLO (single logout) functional**
   - Open console in two browser windows logged in as the same user
   - Click logout in window 1 → redirects to landing page
   - In window 2, refresh or make any API call
   - Should be redirected to `/login` or landing (session killed)
   - If still logged in, check Auth0 logout URL configuration (item 3)

8. **Token revocation kills console session**
   - Log in to console as a test user
   - Auth0 Dashboard → User Management → Users → [test user] → Sessions → Log Out
   - Return to console browser tab and refresh
   - Should be redirected to `/login` within 1 minute (cookie invalidated)
   - If still logged in after 2 minutes, session cache TTL may be too long

---

## When to Run

- **Before every release** to production
- **After any Auth0 environment variable rotation** (domain, audience, client secret)
- **Weekly during closed beta** to catch configuration drift
- **When users report auth failures** (verify items 1, 5, 6 first)

---

## Last Run

```yaml
date: 
result: PASS / FAIL
notes: |
  
run_by: 
```
