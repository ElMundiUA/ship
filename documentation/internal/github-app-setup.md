# GitHub App "Ship" — operator setup checklist

> **Status:** required for the pilot WOW-onboarding flow (see [pilot-plan.md](./pilot-plan.md) §"Day 1").
> **Audience:** ops / platform engineer registering the App in our GitHub org and wiring it into the deployment.
> **Time:** ~15 minutes the first time, ~3 minutes per environment after.

This is the **manual, one-time** half of the GitHub App integration. The
automated half (JWT minting, OAuth state tokens, webhook verification,
installation persistence) lives in [`backend/app/integrations/github/`](../../backend/app/integrations/github)
and is already implemented; this file just documents the GitHub-side
clicking nobody can avoid.

---

## TL;DR

1. Register a GitHub App in our org (one App per *deployment*, not per *customer*).
2. Generate a private key + webhook secret, drop them into the env.
3. Fill the five `GITHUB_APP_*` env vars + `SHIP_CONSOLE_URL`.
4. Install the App on a test repo; confirm the wizard lands on
   `step=github&github=installed`.

---

## 1. Register the App

GitHub UI path: **your org → Settings → Developer settings → GitHub Apps → New GitHub App**.

| Field | Value | Notes |
|---|---|---|
| **GitHub App name** | `Ship` (prod), `Ship — staging`, `Ship — dev-<initials>` | Must be globally unique on github.com. The *slug* derived from this lands in `GITHUB_APP_SLUG` (lowercased, hyphenated). |
| **Homepage URL** | `https://ship.<your-domain>` | Anything reachable; GitHub only displays it. |
| **Callback URL** | `https://api.ship.<your-domain>/v1/integrations/github/install/callback` | Single-value field. **Must** match the API origin where the backend serves; not the console origin. |
| **Setup URL** | *(leave blank)* | We don't use post-install setup; the callback above already handles redirects. |
| **Redirect on update** | ☑ | So repo-selection edits also bounce back through our callback. |
| **Webhook URL** | `https://api.ship.<your-domain>/v1/webhooks/github` | Same host as the callback. For local dev, expose with `cloudflared` / `ngrok` (see §"Local dev" below). |
| **Webhook secret** | `openssl rand -hex 32` → paste here | Save the same value to `GITHUB_APP_WEBHOOK_SECRET`. We HMAC-verify every delivery against this. |
| **SSL verification** | Enabled | Default. |

### Permissions

Repository permissions:

| Permission | Access | Why |
|---|---|---|
| Pull requests | Read & write | Comment, label, request reviews |
| Contents | Read-only | List files, fetch raw blobs for AI context |
| Metadata | Read-only | Mandatory; granted automatically |
| Issues | Read & write | Tracker integration when GH Issues is the chosen backend |
| Workflows | Read-only | Inspect `.github/workflows/` for the wizard's "Approve workflows" step |
| Checks | Read-only | Show CI status next to PRs |
| Actions | Read-only | Read `workflow_run` history |

Organization permissions: none required for the pilot.

User permissions: none.

### Subscribe to events

> **Heads-up:** `installation` and `installation_repositories` are **not**
> in the "Subscribe to events" checklist — GitHub delivers them
> automatically to every App that has a Webhook URL + secret configured.
> Don't go looking for them. (`Installation target` in the list is a
> different event about App ownership transfer; leave it unticked.)

Tick the optional events we actually consume:
- `pull_request`
- `pull_request_review`
- `workflow_run`
- `check_run`
- `issues` (only if GH Issues tracker is in scope)

Day 3 wires `pull_request` and `workflow_run` end-to-end (they populate
the dashboard's Recent PRs / Workflow runs blocks). The rest are
received and signature-verified but treated as no-ops until later
packages land the review / CI handlers. Subscribing now saves a
re-deploy of the App later.

### Where can this GitHub App be installed?

**Any account.** This is what makes the public install URL
(`https://github.com/apps/<slug>/installations/new`) work for arbitrary
customer orgs in the WOW-onboarding flow. If you're registering a
*staging* App you may keep this restricted to your own org.

---

## 2. Collect the secrets

After clicking **Create GitHub App** GitHub drops you on the App's
"About" page. Collect:

1. **App ID** (numeric, top of the page) → `GITHUB_APP_ID`.
2. **Client ID** (`Iv1.xxxxxxxxxxxx`, "About" section) → `GITHUB_APP_CLIENT_ID`.
3. **Client secret**: **Generate a new client secret** → copy once → `GITHUB_APP_CLIENT_SECRET`.
   GitHub will never show it again; if you lose it, regenerate.
4. **Private key**: scroll down → **Generate a private key** → downloads
   `<slug>.<date>.private-key.pem` → store the **whole file content**
   (including the `-----BEGIN RSA PRIVATE KEY-----` / `-----END ...-----`
   lines and trailing newline) into `GITHUB_APP_PRIVATE_KEY`.
   - In `.env` files multi-line values must be wrapped in single quotes,
     e.g. `GITHUB_APP_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----\n'`.
   - In Bunny Magic Containers / k8s secrets, paste the raw multi-line
     string directly into the secret value field.
5. **Slug**: read it off the App's URL bar
   (`https://github.com/organizations/<org>/settings/apps/<slug>`) →
   `GITHUB_APP_SLUG`. Defaults to `ship` in `Settings`; override per env.

---

## 3. Wire the env

`.env` (or wherever your deployment loads its config from):

```env
# --- GitHub App (Day-1 WOW onboarding) ---
GITHUB_APP_ID=123456
GITHUB_APP_PRIVATE_KEY='-----BEGIN RSA PRIVATE KEY-----
MIIEpAIB...
-----END RSA PRIVATE KEY-----
'
GITHUB_APP_CLIENT_ID=Iv1.aaaaaaaaaaaa
GITHUB_APP_CLIENT_SECRET=bbbbbbbbbbbbcccccccccccc
GITHUB_APP_WEBHOOK_SECRET=<the openssl-generated hex from §1>
GITHUB_APP_SLUG=ship

# --- Console origin (for callback redirects) ---
# Browser-facing, NOT the API origin. The install callback hits
# /v1/integrations/github/install/callback on the API and then redirects
# the user's browser tab back here to /onboarding.
SHIP_CONSOLE_URL=https://ship.<your-domain>
```

Restart `ship-server` (and `console`, if `SHIP_CONSOLE_URL` changed).

> **Tip:** missing or malformed `GITHUB_APP_*` vars surface as a
> `503 — GitHub App is not configured on this deployment` from
> `POST /v1/integrations/github/install/start`, which the console
> renders as a friendly *"GitHub App env vars are missing on the
> backend"* banner on the wizard's GitHub step.

---

## 4. Smoke test

Once env is loaded:

1. Sign into the console as a workspace **admin**.
2. Run the onboarding wizard until the **GitHub** step.
3. Click **Install Ship on GitHub →** → you should bounce to
   `github.com/apps/<slug>/installations/new`.
4. Pick a repo (default), click **Install**.
5. GitHub sends you to `/v1/integrations/github/install/callback?...`,
   which redirects back to
   `<SHIP_CONSOLE_URL>/onboarding?step=github&github=installed`.
6. The wizard renders the aqua **"GitHub App installed."** banner.
   The button label flips to **"Reinstall / pick more repos →"**.
7. Confirm the row landed in Postgres:
   ```sql
   SELECT installation_id, account_login, account_type, installed_at
     FROM github_installations
    ORDER BY installed_at DESC LIMIT 5;
   ```
8. Trigger a webhook by uninstalling and reinstalling — `suspended_at`
   should toggle non-null, then null again, on each event.

---

## 5. Local dev

For laptop development the App still needs a public webhook URL — GitHub
won't deliver to `localhost`. Recommended path:

1. `cloudflared tunnel --url http://localhost:8100` → grab the
   `https://<random>.trycloudflare.com` hostname.
2. Either register a *separate* "Ship — dev-<initials>" App pointing at
   that hostname (cleanest), or `Edit` your dev App's webhook URL each
   morning when the tunnel hostname rotates.
3. Set `SHIP_PUBLIC_URL=https://<random>.trycloudflare.com` so callback
   URLs the backend mints stay consistent with what GitHub knows.
4. Set `SHIP_CONSOLE_URL=http://localhost:3001` so the post-install
   redirect lands in your local Next.js tab, not the tunnel.

`SHIP_AUTH_MODE=local` is fine for testing the install flow; the
callback path is auth-less by design (it's authorised via the signed
`state` token, not a session).

---

## 6. Rotating credentials

| Secret | When to rotate | How |
|---|---|---|
| Private key (`.pem`) | Yearly, or after a suspected leak | App settings → "Generate a private key" → swap the env var → both old and new keys keep working until you click "Delete" on the old one. Zero-downtime. |
| Webhook secret | Yearly | App settings → edit webhook secret → swap env. There is a brief window where in-flight deliveries with the old secret will fail HMAC; GitHub will retry with the new one. |
| Client secret | Yearly | App settings → "Generate a new client secret" → swap env. The old one keeps working until explicitly revoked. |

Never rotate `GITHUB_APP_ID` — it's the App's identity, not a secret.

---

## 7. References

- [GitHub Apps overview](https://docs.github.com/apps/creating-github-apps/about-creating-github-apps/about-creating-github-apps)
- [Authenticating as a GitHub App](https://docs.github.com/apps/creating-github-apps/authenticating-with-a-github-app/about-authentication-with-a-github-app)
- [Webhook delivery security](https://docs.github.com/webhooks/using-webhooks/validating-webhook-deliveries)
- Backend implementation: [`backend/app/integrations/github/`](../../backend/app/integrations/github)
- Console wizard: [`console/src/app/onboarding/page.tsx`](../../console/src/app/onboarding/page.tsx) (step `github`)
- Settings schema: [`backend/app/core/config.py`](../../backend/app/core/config.py) (search for `github_app_`)
