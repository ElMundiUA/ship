# Notification-seam operator runbook (ELS-226)

The notification seam (`app/services/notify.py`, Phase 1 of the
headless pivot) routes every engine emission ("Ship tells a human
something") to per-workspace channels: **Inbox** (always), **Linear
comment**, **email**. Launch posture: **inbox-only everywhere** — the
seam is a no-op until you opt a workspace in AND flip the global
switch.

## Enable (two keys, both required)

1. **Global kill-switch** (env, backend deployment):

   ```
   SHIP_NOTIFY_CHANNELS=true
   ```

   While unset/false, `notify()` forces inbox-only for every workspace
   regardless of their settings (the router never even consults them).

2. **Per-workspace routing** (`workspaces.settings` JSON — via the
   config surface or SQL):

   ```json
   {
     "notifications": {
       "email_to": "ops@example.com",
       "channels": {
         "info":    ["inbox"],
         "action":  ["inbox", "linear"],
         "blocker": ["inbox", "linear", "email"]
       }
     }
   }
   ```

   Rules baked into the router (`notify_config.get_channel_routing`):
   - `inbox` is ALWAYS prepended even if omitted — a typo can never
     silently drop an engine emission.
   - Unknown channel strings are skipped with a logged warning.
   - Malformed shapes fail closed to inbox-only (warning, never raise).
   - Missing `email_to` → the email channel does a structured skip
     (`skipped_no_email_to`); it never guesses a recipient.

## Verify

Every `notify()` call writes one best-effort audit row:

```sql
SELECT created_at, target_id, payload
FROM audit_log
WHERE workspace_id = :ws AND action = 'notify.emit'
ORDER BY created_at DESC LIMIT 20;
```

`payload.requested_channels` shows what the router resolved;
`payload.results[]` carries per-channel `ok` / `skipped` / `detail`.
A failing channel shows `ok=false` with the error in `detail` — the
Inbox letter still lands (fan-out is isolated per channel).

## Email transport smoke (do once before opting any workspace in)

The recording-sender tests prove rendering, not transport. Prove the
real path once per environment:

```
RUN_EMAIL_SMOKE=1 EMAIL_SMOKE_TO=you@example.com \
  pytest apps/backend/tests/test_services_notify.py::test_real_email_transport_smoke
```

(Needs the environment's real email creds — e.g. `SENDGRID_API_KEY` —
in the env; the test asserts `EmailDeliveryResult.sent`, then check
the mailbox.) Reminder: per the bootstrap-intelligence postmortem, an
assumed-wired transport can silently fail-closed in prod — do not skip
this step.

## Rollback (two levers, no deploys needed for #2)

1. **Instant global**: set `SHIP_NOTIFY_CHANNELS=false` (or unset) and
   restart the backend → every workspace is inbox-only again. Zero code,
   zero data migration; Inbox behavior is unchanged because it never
   left.
2. **Per-workspace / per-level**: edit the workspace's
   `settings.notifications.channels` — e.g. drop `"email"` from
   `blocker` — takes effect on the next emission, no restart.

## Invariants the seam must keep (do not "fix" these)

- Comments/email are **egress only** — nothing reads them back as a
  transition signal (`tracker_fsm.py:277`: STATUS is the only signal).
- Site-level dedup (audit windows, intake handles) lives in the
  CALLERS, not the seam.
- `notify()` never raises into the engine path; a failed audit write
  never rolls back the emit.
