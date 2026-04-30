# E09 — SendGrid email pipeline + 4 templates

**Priority:** P2
**Effort:** M (~3–4 days)
**Owner:** TBD

## Goal

The backend sends production-grade transactional email via SendGrid. Four templates ship live: **invite**, **inbox-new-item**, **run-failure**, **daily-digest**. SPF/DKIM passes for `mail.ship.elmundi.com`. Every email has an unsubscribe / preferences link where appropriate.

## Why

The product depends on async ownership signals reaching humans. Without email, an Inbox item nobody opens the console for is invisible. The maintainer already has a SendGrid account.

The codebase has placeholder modules at `backend/app/services/email/` and references in `services/notifications.py`. They mostly do nothing today (or use Mailosaur in tests). Productionization is the work.

## Tasks

### T01 — SendGrid account hygiene **[S]**

- Confirm the SendGrid account in use, single sender domain, API key with **Mail Send** scope only.
- Domain authentication: SPF, DKIM, DMARC records for `mail.ship.elmundi.com` (a subdomain dedicated to outbound).
- Update env vars: `SENDGRID_API_KEY`, `SENDGRID_FROM_EMAIL=hello@mail.ship.elmundi.com`, `SENDGRID_FROM_NAME=Ship`.
- Document in `documentation/internal/operations/sendgrid-setup.md`.

**Acceptance:** `mail-tester.com` score 9+/10 on a sample send.

### T02 — Email service wrapper **[S]**

- File: `backend/app/services/email/sendgrid_client.py`.
- Thin wrapper around SendGrid v3 API: `send(to, template_id, dynamic_data, reply_to)`.
- Retry on 429/5xx with exponential backoff.
- Test mode: when `EMAIL_MODE=capture`, write to `output/email-capture/{timestamp}.json` instead of sending. Used in dev + e2e.
- All templates load from SendGrid Dynamic Templates (managed in their UI), not from local Jinja.

**Acceptance:** unit tests cover send / retry / capture mode.

### T03 — Template: invite **[S]**

- SendGrid Dynamic Template `d-INVITE_xxx`.
- Subject: "You're in — closed beta access to Ship".
- Body: short, cordial, linking to `https://app.ship.elmundi.com/invite/{token}`. Token TTL 14 days. Unsubscribe omitted (transactional).
- Trigger: `POST /v1/admin/invites` (E08 dependency).

**Acceptance:** maintainer sends a real invite to a personal email and lands in `/invite/{token}`.

### T04 — Template: inbox-new-item **[M]**

- SendGrid Dynamic Template `d-INBOX_NEW_xxx`.
- Subject: `[{workspace}] {item.shape} needs you: {item.title}`.
- Body: item summary, "what is being asked for", direct link to `/inbox/{id}`, deeplink to the related repo / tracker.
- Send rules:
  - Send only when an item is `assigned` and the assignee has `email_notifications.inbox: true`.
  - Throttle: at most one email per assignee per 5 minutes (batch into a single digest if more arrive).
  - Snooze the email if the item is resolved before the 5-minute throttle expires.

**Acceptance:** intentional clarification from agent → assignee receives email within 5 minutes.

### T05 — Template: run-failure **[S]**

- SendGrid Dynamic Template `d-RUN_FAIL_xxx`.
- Subject: `[{workspace}] Run failed: {routine_name}`.
- Body: failure summary, link to the dispatched workflow, "Retry" link to the console run page.
- Send rule: only to repo maintainers / on-call group; once per failed run, no spam on retries.

**Acceptance:** intentionally failed run produces one email.

### T06 — Template: daily-digest **[M]**

- SendGrid Dynamic Template `d-DAILY_DIGEST_xxx`.
- Subject: `Ship daily — {workspace} — {date}`.
- Body sections:
  - Inbox: open items by shape; aging items (>3d).
  - Runs: 24h run summary (success / fail / skipped).
  - Knowledge: top 3 articles touched by agents.
  - Decisions: anything resolved in the last 24h.
- Send rule: 09:00 user local time (or UTC for closed beta).
- Generation: a daily cron / ARQ job at 08:50 UTC computes per-workspace digest and queues sends. Job lives in `backend/app/services/email/daily_digest.py`.

**Acceptance:** a workspace with activity gets a non-empty digest at the scheduled time. Empty workspaces are skipped (we don't send "nothing happened" emails in beta).

### T07 — User notification preferences **[S]**

- New table or column on `users`: `email_preferences jsonb`.
- Console page in `console/src/app/settings/notifications/page.tsx`: toggles for each shape.
- Default: invite + run-failure on; inbox-new-item + daily-digest on.

**Acceptance:** toggling off stops the email.

### T08 — Unsubscribe / list management **[S]**

- For non-transactional digests: include unsubscribe link.
- Click-through hits a backend endpoint that flips the user's preference and confirms.

**Acceptance:** unsubscribe works in one click and the user immediately stops receiving daily digests.

### T09 — Failure mode tests **[S]**

- SendGrid down → email queued and retried (use ARQ / BackgroundTasks chain).
- Bounce / spam report webhooks from SendGrid → record on the user, suppress further sends.

**Acceptance:** simulated bounce results in suppression.

### T10 — Documentation update **[S]**

- `documentation/troubleshooting.md` — section "I'm not receiving emails".
- `documentation/internal/operations/sendgrid-runbook.md` — how to rotate keys, how to inspect logs.

**Acceptance:** runbook exists and is reachable.

## Definition of done

- [ ] All 4 templates live in SendGrid and triggered by their respective backend events.
- [ ] Domain authenticates with SPF/DKIM/DMARC.
- [ ] `EMAIL_MODE=capture` works in dev and e2e.
- [ ] Mailosaur tests in `e2e/` updated to use new templates where relevant.
- [ ] Bounce / suppression handled.
- [ ] Notification preferences page in console.

## Risks / unknowns

- SendGrid template IDs need to be in env vars, not hardcoded; handle the dev/staging/prod fan-out.
- Mailosaur tests may need new selectors after templates change.
- Daily digest computation can be expensive on busy workspaces; cap query window and paginate inbox sections.

## Out of scope

- Slack / Teams notifications.
- Transactional in-app notifications (already partly there via `notifications.py` — extend in a different epic if needed).
- Marketing email (newsletter).
- SMS / push notifications.
