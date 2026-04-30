# E10 — Observability + KPI dashboard + alerts

**Priority:** P2
**Effort:** S (~2–3 days)
**Owner:** TBD

## Goal

The maintainer can answer in 30 seconds: "Is Ship up? How many users finished onboarding today? Any failed runs in the last hour? Where do users drop off?". Failures page within 5 minutes. Sentry events carry enough context to be debuggable.

## Why

Without this, beta users will hit broken paths and the maintainer will only learn about it days later when someone happens to email. Operational floor is non-negotiable for letting strangers in.

Sentry is already wired in `backend/app/core/sentry.py` and `console/sentry.*.config.ts`. What's missing: context tagging, alert rules, an uptime monitor, and a single KPI view.

## Tasks

### T01 — Sentry context audit **[S]**

- Backend: every `/v1/*` request should set `workspace_id`, `user_id`, `route`, `request_id` on the event scope. Verify `services/sentry.py` (or middleware) does this.
- Console: Sentry-React should set the same. Server actions also.
- Filter out PII: never include email or names in event context (only IDs).

**Acceptance:** sample bad request results in a Sentry event tagged with workspace + user + route.

### T02 — Sentry alert rules **[S]**

- Rule 1: any `5xx` from `/v1/auth/*` more than 3 in 10 min → ping maintainer (email).
- Rule 2: `/v1/integrations/github/install/callback` errors → ping maintainer.
- Rule 3: any exception in `services/inbox/side_effects.py` → ping maintainer (these silently corrupt evidence).
- Rule 4: any exception in `services/email/sendgrid_client.py` → ping maintainer.
- All other backend exceptions → daily summary.

**Acceptance:** rules in the Sentry project. Test by deliberately triggering one.

### T03 — Uptime monitor **[S]**

- Sign up for Better Uptime (free tier ≥ 10 monitors).
- Monitors:
  - `https://ship.elmundi.com/` — landing
  - `https://app.ship.elmundi.com/login` — console
  - `https://app.ship.elmundi.com/api/health` (or whatever console exposes)
  - `https://api.ship.elmundi.com/v1/health`
  - `https://api.ship.elmundi.com/v1/health` deep — verifies DB round-trip
- 1-minute interval, alert maintainer email on 2 consecutive failures.
- Public status page: `status.ship.elmundi.com`.

**Acceptance:** all monitors green; a deliberate downtime triggers the alert.

### T04 — KPI views in Postgres **[S]**

- Create SQL views (or Postgres materialized views) for:
  - `kpi_signups_daily` — `date_trunc('day', created_at)` grouped count of `users`.
  - `kpi_onboardings_completed_daily` — workspaces that have one+ activated repo + bound tracker.
  - `kpi_active_workspaces_7d` — workspaces with ≥1 dashboard load OR ≥1 run in last 7 days.
  - `kpi_runs_daily` — runs by status from `pipeline_runs`.
  - `kpi_inbox_resolution_time` — median `disposed_at - created_at` for resolved items.
- Lives in `backend/migrations/versions/00xx_kpi_views.py`.

**Acceptance:** `psql` queries against each view return reasonable numbers.

### T05 — Internal KPI page **[S]**

- File: new `console/src/app/admin/kpi/page.tsx` (admin-only, gated by `is_platform_admin`).
- Renders the 5 KPI views as numeric cards + 30-day sparklines.
- Auto-refresh every 5 min.
- No fancy chart library — `<svg>` sparklines are fine.

**Acceptance:** the maintainer's own user can load `/admin/kpi` and see numbers.

### T06 — Application logs aggregation **[S]**

- Bunny container logs are not durable; ship them somewhere.
- Cheapest path: pipe stdout/stderr to Bunny's log retention (if supported) OR add a simple Logflare / BetterStack Logs destination.
- Decide: keep Sentry as primary error sink + minimal log aggregation OR full log shipping.
- For closed beta: Sentry + 7d log retention in Bunny is enough. Document the limit.

**Acceptance:** the maintainer can grep last 24h of backend logs in under 1 minute.

### T07 — Frontend Web Vitals **[S]**

- Console + landing: enable Sentry's `BrowserTracing` (already in config), confirm `LCP`, `FID`, `CLS` reported.
- No alerts; just visibility for E12 (UX polish) decisions.

**Acceptance:** Web Vitals dashboard populated with real production data.

### T08 — Runbook **[S]**

- File: `documentation/internal/operations/runbook.md`.
- "Ship is down" — first 5 commands to run.
- "Sentry alert fired" — which dashboard to check, who to ping.
- "User reports broken signup" — which logs, which Sentry filter.
- "Database is slow" — `pg_stat_activity` queries, indexes to inspect.

**Acceptance:** runbook exists and was followed once during a deliberate dry-run failure.

## Definition of done

- [ ] Sentry events have workspace + user + route context.
- [ ] 4 alert rules live and tested.
- [ ] Uptime monitor green; alert tested.
- [ ] 5 KPI views and `/admin/kpi` page live.
- [ ] Logs accessible for the last 24h.
- [ ] Runbook merged.

## Risks / unknowns

- Sentry free / team plan event quota — keep an eye on errors-per-minute as adoption grows.
- Better Uptime free tier may not support 1-minute interval; if not, drop to 3 minutes.
- KPI views on Postgres may compete with app queries on a small instance — use materialized + scheduled refresh if it gets hot.

## Out of scope

- Distributed tracing / OpenTelemetry collector (post-beta).
- Custom Grafana / Metabase dashboard (the in-app KPI page is enough for closed beta).
- SLO / error-budget framework.
- Real APM (Datadog / New Relic).
- Multi-region failover.
