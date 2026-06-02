# Deploy feature — setup checklist (DigitalOcean)

This is the **operator setup** for the new one-click Deploy feature. Do
these steps once; the values go into your **gitignored `.env`** at the repo
root. **Never commit real secrets** — `.env` is ignored by git on purpose.

Status of the feature while you read this:
- ✅ **Connect** (DigitalOcean OAuth) — built. Needs the OAuth app below to work.
- ✅ **Plan** (LLM analyzes the repo → deploy plan) — built. Needs an LLM key below.
- ⏳ **Execute** (create the App Platform app) + **Track/health** + **console Deployments tab** — in progress.

---

## 1. Register a DigitalOcean OAuth Application  ← required for "Connect"

1. Go to **https://cloud.digitalocean.com/account/api/applications** →
   **Register a new OAuth Application**.
2. Fill in:
   - **Application name:** `Ship Deploy` (anything).
   - **Homepage / Application URL:** your console URL
     (local: `http://localhost:3001`).
   - **Redirect / Callback URL** — must be **exactly**:
     ```
     <SHIP_PUBLIC_URL>/v1/integrations/digitalocean/install/callback
     ```
     Local default:
     ```
     http://localhost:8100/v1/integrations/digitalocean/install/callback
     ```
   - **Scopes:** select **`read`** and **`write`** (write is required to
     create/manage App Platform apps).
3. After saving you get a **Client ID** and **Client Secret**. Copy both.

Put them in `.env`:
```dotenv
DIGITALOCEAN_CLIENT_ID=<client id from DO>
DIGITALOCEAN_CLIENT_SECRET=<client secret from DO>
# optional — defaults shown:
# DIGITALOCEAN_OAUTH_SCOPES=read write
# DIGITALOCEAN_TOKEN_REFRESH_HOURS=24
```
If these are unset, the connect endpoint returns a clean `503` (the rest of
the app is unaffected).

---

## 2. LLM for the deploy planner  ← required for "Plan"

The planner reuses Ship's configured agent vendor.

**Production / normal:** set one of these in `.env`:
```dotenv
# OpenAI (default vendor)
OPENAI_API_KEY=...
# or Anthropic
ANTHROPIC_API_KEY=...
AGENT_VENDOR=anthropic
```

**Local-dev Gemini fallback — ⚠️ DO NOT DEPLOY THIS PART**

If your laptop has neither key, you can run the planner against a personal
Gemini key. This path is **gated** and **must stay off in any deployed
environment**:
```dotenv
DEPLOY_PLANNER_GEMINI_API_KEY=<your personal Gemini key>   # .env only — never commit
DEPLOY_PLANNER_ALLOW_DEV_FALLBACK=true
# optional:
# DEPLOY_PLANNER_GEMINI_MODEL=gemini-2.5-flash
```
When this fallback runs it logs a loud warning. Leave
`DEPLOY_PLANNER_ALLOW_DEV_FALLBACK` unset (false) everywhere except your
laptop, and do **not** add the Gemini key to any deployed config.

---

## 3. Apply the database migration

A new provider value (`digitalocean`) was added to the native-integration
provider check constraint.
```bash
make db-upgrade        # or: PYTHONPATH=apps alembic -c apps/backend/alembic.ini upgrade head
```
Expected head: `0081_native_provider_do`.

---

## 4. Notes for the first real deploy (your Streamlit app)

- **Public repo** → deploys with **zero extra auth**: the App Platform spec
  uses a plain `git` source (clone URL), no DigitalOcean↔GitHub link needed.
  Easiest path for the first end-to-end test.
- **Private repo** → DigitalOcean's own GitHub integration must be
  authorized on the repo (a one-time step in the DO dashboard). The plan
  will surface this as a warning. We can automate the prompt for it later.
- Streamlit is deployed as one **service**: Python buildpack from
  `requirements.txt`, `run_command = streamlit run <entry>.py
  --server.port 8080 --server.address 0.0.0.0`, health check on
  `/_stcore/health`.

---

## Quick checklist

- [ ] DO OAuth app registered; redirect URL matches `<SHIP_PUBLIC_URL>/v1/integrations/digitalocean/install/callback`
- [ ] `DIGITALOCEAN_CLIENT_ID` / `DIGITALOCEAN_CLIENT_SECRET` in `.env`
- [ ] An LLM key in `.env` (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`, or the gated Gemini dev fallback)
- [ ] `make db-upgrade` run (head `0081_native_provider_do`)
- [ ] Streamlit example repo is **public** (simplest first test) or DO↔GitHub authorized
