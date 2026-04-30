# Closed-beta invite runbook

**Audience:** Maintainer (Denys) handling the closed-beta cohort by hand.

This runbook describes the manual procedure for moving someone from the
public waitlist into the platform. It is intentionally hand-curated for
the closed beta — a console UI for invite management is post-beta.

## Daily / weekly cadence

The expected cadence in closed beta is **once a week**:

- Monday morning, walk the waitlist queue.
- Approve up to N new invites where N respects the soft cap
  (`CLOSED_BETA_CAP`, default 50).
- Send invites; archive the rest with a reason.

Soft cap visible at `https://ship.elmundi.com/getting-started` as
"X / 50 closed-beta seats taken" — sourced from
`GET /v1/public/beta-capacity`.

## Step 1 — Pull pending waitlist submissions

Sign in to the backend admin shell (Bunny Magic Container console) or run
locally with the prod `DATABASE_URL`:

```bash
psql "$DATABASE_URL" -c "
  SELECT email, role, tracker, agent, note, created_at
  FROM waitlist_submissions
  WHERE NOT EXISTS (
    SELECT 1 FROM platform_invites pi
    WHERE pi.email = waitlist_submissions.email
  )
  ORDER BY created_at ASC
  LIMIT 25;
"
```

This lists waitlist signups that have **not yet been issued** an invite.

## Step 2 — Decide each row

For each row, pick one of:

- **Approve** — mint an invite (Step 3).
- **Defer** — leave for the next cohort. Annotate in your own log.
- **Decline** — note in the row (no UI for this yet; an internal note
  table or a personal log is enough).

Approval criteria for closed beta:

- Email looks legitimate (not throwaway).
- Use case described in `note` aligns with what Ship does today.
- Capacity has not yet hit `CLOSED_BETA_CAP`.

## Step 3 — Mint the invite

Mint a platform invite via the admin API. You'll need a session token
or PAT with `is_platform_admin = true`.

```bash
curl -X POST "https://ship.elmundi.com/v1/admin/invites" \
  -H "Authorization: Bearer $SHIP_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "newuser@example.com",
    "expires_in_days": 14,
    "note": "approved 2026-04-30 — ElMundi referral"
  }'
```

Response includes the **raw token** exactly once. The landing link is
returned in the same response (`link` field). It looks like:

```
https://app.ship.elmundi.com/invite/<token>
```

Copy the link. The raw token is **not stored** — only its hash. If you
lose the link, mint a new invite and revoke the old one.

## Step 4 — Send the invite email

> ⚠️ **TODO** — once SendGrid lands (E09), invites are sent automatically
> when the admin API mints them. Until then, email manually using the
> SendGrid template, your personal Gmail, or any plain SMTP relay.

Hand-send template (paste into your mail client):

> **Subject:** You're in — closed beta access to Ship
>
> Hi {{first name}},
>
> You're invited to the Ship closed beta. Use the link below to sign in
> and bootstrap your workspace. The invite expires in 14 days.
>
> {{landing link}}
>
> Reply to this email with anything you'd like us to know about your
> setup. We're a small team and read every reply during the beta.
>
> — Ship

## Step 5 — Track the cohort

Periodically (weekly), eyeball:

```bash
psql "$DATABASE_URL" -c "
  SELECT
    COUNT(*) FILTER (WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()) AS pending,
    COUNT(*) FILTER (WHERE accepted_at IS NOT NULL) AS accepted,
    COUNT(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked,
    COUNT(*) FILTER (WHERE accepted_at IS NULL AND expires_at <= now()) AS expired
  FROM platform_invites;
"
```

If `pending` grows faster than `accepted`, the email step is failing —
investigate before approving more rows.

## Step 6 — Revoking an invite

If someone leaves, requests revocation, or the email turns out to be
wrong:

```bash
curl -X POST "https://ship.elmundi.com/v1/admin/invites/$INVITE_ID/revoke" \
  -H "Authorization: Bearer $SHIP_ADMIN_TOKEN"
```

The `accepted_at` field is preserved, but the user can no longer use
the link. If the user already accepted, they keep their workspace —
revocation gates *future* use of the link, not past usage.

## Failure modes & recovery

| Symptom | Likely cause | Recovery |
|---|---|---|
| User says "invite link 404s" | Token expired | Mint new invite |
| User clicks link, sees "no longer valid" | Revoked or accepted twice | Re-issue if a mistake |
| User signs in, sees 403 "needs invite" | Email mismatch (Auth0 sub vs invite email) | Check the email Auth0 returned matches the invited email; re-issue with the correct casing if needed |
| Invite minted, no email arrives | SendGrid (E09) not yet wired | Hand-send the link |
| Capacity reads "0 / 50" but admin sees pending | Replication lag or cache | Hit the endpoint twice; if persistent, restart the backend container |

## Related runbooks

- [Auth0 pre-launch checklist](../auth0-checklist.md)
- [Auth0 prod config audit](../auth0-prod-config.md)
- (Coming soon) SendGrid runbook — see closed-beta plan E09
