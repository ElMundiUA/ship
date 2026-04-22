---
rfc: 0003
title: "Telemetry and feedback"
status: Accepted
created: 2026-04-17
---

# RFC-0003 — Telemetry and feedback

## Summary

Ship accepts two streams of signal from adopters: opt-in anonymous telemetry about artifact usage, and explicit feedback submissions that become GitHub issues on the Ship repository. Both streams are driven by the same CLI (`shipctl`) and ride the same HTTP surface. The client stays in control end to end: telemetry is OFF by default, feedback is always drafted locally before it is sent, and nothing leaves the repository without an explicit action.

The goal is tight: understand which artifacts adopters actually consume, which gaps they hit, and what corrections they propose — without ever exfiltrating proprietary code, ticket text, or repository metadata.

## Opt-in

Telemetry is an explicit opt-in with two codepaths:

1. **Interactive.** `shipctl init` asks once, in plain language:
   ```
   Share anonymous telemetry (artifact usage + feedback metadata)? [y/N]
   ```
   Default is `N`. The answer is written to `telemetry.share` in `.ship/config.yml`.
2. **Non-interactive.** Any non-TTY run (`shipctl init --yes`, CI) defaults to OFF. The user must explicitly set `telemetry.share=true` in config or via `SHIP_TELEMETRY=true`.

CLI switches:

| Command                        | Behavior                                                         |
|--------------------------------|------------------------------------------------------------------|
| `shipctl telemetry on`         | Sets `telemetry.share=true`.                                     |
| `shipctl telemetry off`        | Sets `telemetry.share=false`. Also flushes the local outbox.     |
| `shipctl telemetry status`     | Prints the current state and counts in the outbox.               |
| `shipctl telemetry reset-id`   | Generates a new `anonymous_id` and discards the old one locally. |

With `telemetry.share=false`, `shipctl` never writes to the outbox and never calls `POST /telemetry`. The scope flags under `telemetry.scope.*` are evaluated only when the master switch is `true`.

## Events

All events share a shape: an event `type`, the `anonymous_id`, a UTC timestamp, and a per-event payload. `shipctl` adds its own version (`shipctl_version`) and the active `stack.preset` to every event. (The on-the-wire field is `type`; older drafts called it `event`.)

| Event `type`       | Trigger                                               | Payload fields                                                                             |
|--------------------|-------------------------------------------------------|--------------------------------------------------------------------------------------------|
| `artifact.fetch`   | successful `shipctl <kind> fetch`                     | `anonymous_id, kind, id, version, source (cache\|network), ttl_age_h`                      |
| `artifact.use`     | `shipctl <kind> show <id> --used-by <agent>`          | `anonymous_id, kind, id, version, agent`                                                   |
| `artifact.sync`    | `shipctl sync` completes                              | `anonymous_id, categories, updates_count, failures_count`                                  |
| `feedback.submit`  | `shipctl feedback submit`                             | `anonymous_id, artifact:{kind,id,version}, summary, suggestion, stack:{tracker,ci,agents,preset}` |
| `doctor.result`    | `shipctl doctor` if `scope.errors=true`               | `anonymous_id, stack, findings_hash`                                                       |

`kind` in every event payload is one of `pattern | tool | collection | doc`
— the authoritative list lives in `cli/lib/commands/feedback.mjs`
(`ALLOWED_KINDS`) and `cli/lib/config/schema.mjs` (`KINDS`). The
`workflow` kind was retired by
[RFC-0007](/docs/protocol/rfc-0007-lanes-and-run-agent) Phase 6 and
MUST NOT appear in feedback envelopes or telemetry payloads.

Examples:

```json
{
  "type": "artifact.fetch",
  "ts": "2026-04-17T10:05:13Z",
  "anonymous_id": "b4a7...-v4",
  "shipctl_version": "0.3.2",
  "stack_preset": "web-app",
  "payload": {
    "kind": "pattern",
    "id": "role-developer",
    "version": "1.4.2",
    "source": "network",
    "ttl_age_h": 0
  }
}
```

```json
{
  "type": "feedback.submit",
  "ts": "2026-04-17T11:40:02Z",
  "anonymous_id": "b4a7...-v4",
  "shipctl_version": "0.3.2",
  "stack_preset": "web-app",
  "payload": {
    "artifact": { "kind": "pattern", "id": "role-developer", "version": "1.4.2" },
    "summary": "Developer PR checklist misses mobile preview step",
    "suggestion": "Add a 'mobile preview attached' bullet under evidence.",
    "stack": { "tracker": "linear", "ci": "gh-actions", "agents": ["cursor"], "preset": "web-app" }
  }
}
```

## Never sent

The following data is never emitted under any configuration:

- Source code, diffs, or file contents.
- Ticket text, PR descriptions, branch names, or commit messages.
- File paths inside the client repository.
- Git remote URLs, organization names, repository names.
- User email, username, or machine hostname.
- Environment variables other than the narrow set the CLI already reads (`SHIP_*`).

`shipctl` enforces this by construction: telemetry payloads are built from a fixed whitelist of fields. Adding a new payload field requires an RFC amendment.

## Local buffering

Every event is first appended to a local outbox:

```
.ship/telemetry-outbox.jsonl
```

Each line is a self-contained JSON event. The file is ignored by git by default (see RFC-0002 gitignore defaults).

Flush behavior:

- `shipctl` attempts to flush the outbox at the end of every command in a non-blocking way. The rate limit is **60 batches per minute per `anonymous_id`** (not per event); a batch may contain up to 100 events.
- Flush sends up to 100 events per request via `POST /telemetry` as `{"events": [...]}`.
- Every event in a single batch MUST share the same `anonymous_id`; mixed-id batches are rejected by the server with HTTP `400` (see Server endpoint).
- Successfully sent events are removed from the outbox.
- Network errors leave the outbox untouched; the next run retries.
- `shipctl telemetry off` flushes once, then clears the outbox regardless of delivery state.

This makes offline or air-gapped CI safe: events accumulate locally and ship when connectivity returns, without blocking the command that produced them.

## Server endpoint

`POST /telemetry` accepts a batch:

```json
{ "events": [ { "type": "...", "ts": "...", "anonymous_id": "...", "payload": { ... } } ] }
```

Responses:

- `202 Accepted` with `{ "accepted": <n>, "rejected": <m>, "reasons": [ {"index": <i>, "code": "<reason>"} ] }` on success. `accepted + rejected` always equals the batch size; per-event reasons let `shipctl` drop only the offending lines from the outbox.
- `429 Too Many Requests` when the per-`anonymous_id` rate limit is exceeded; `shipctl` backs off until the next flush attempt.
- `413 Payload Too Large` when a single batch exceeds 256 KiB; `shipctl` chunks and retries.
- `400 Bad Request` for whole-batch validation failures: mixed `anonymous_id` across the events in the batch, missing required envelope fields, or any event whose payload includes a key from the denylist (see below).

### Single-id batches

A single batch MUST carry one `anonymous_id` across every event. The
top-level `POST /telemetry` request body MAY include `anonymous_id` once at
envelope level; if present it MUST match every event. Mixed-id batches are
a hard `400` so that the server never has to reason about identity rotation
mid-flush.

### Payload-key denylist

To make accidental leakage impossible at the protocol level, the server
rejects any event whose `payload` contains one of the denylisted keys —
shallow or nested:

```
path, code, diff, branch, remote, email
```

Such a batch fails with `400 Bad Request` and a `reasons` entry of
`{"code": "denylisted_payload_key", "key": "<name>"}`. `shipctl` mirrors the
denylist client-side before writing to the outbox, so a properly-built
batch never trips the server check.

### Rate limit

60 requests per minute per `anonymous_id`. **Per batch, not per event.** A
batch carrying 100 events counts as 1 request.

Storage (suggested Postgres schema):

```sql
create table telemetry_events (
  id              bigserial primary key,
  received_at     timestamptz not null default now(),
  event           text not null,
  anonymous_id    uuid not null,
  ts              timestamptz not null,
  shipctl_version text not null,
  stack_preset    text,
  payload         jsonb not null
);

create index telemetry_events_event_ts_idx on telemetry_events (event, ts);
create index telemetry_events_anon_idx     on telemetry_events (anonymous_id);
create index telemetry_events_payload_gin  on telemetry_events using gin (payload);
```

`anonymous_id` is indexed for revocation and rate-limit checks, not for cross-referencing with identity.

## Feedback draft format

Feedback is always drafted locally as a markdown file before it is sent anywhere:

```
.ship/feedback-drafts/YYYY-MM-DD-HHMMSS-<kind>-<id>.md
```

Each draft has YAML front-matter plus free-form markdown:

```markdown
---
kind: pattern
id: role-developer
version: 1.4.2
tags: ["checklist", "mobile"]
title: "Developer PR checklist misses mobile preview step"
---

## Summary

The developer role prompt lists evidence requirements for backend and web UI
but does not mention mobile preview links.

## Suggestion

Add a bullet under "Evidence" requiring a mobile preview attachment
when the change touches mobile surfaces.

## Context

- Seen on pattern:role-developer@1.4.2 in a web-app preset.
- CI: gh-actions. Tracker: linear. Agent: cursor.
```

Drafts are private until `shipctl feedback submit` is called.

## Submit flow

```
shipctl feedback submit .ship/feedback-drafts/2026-04-17-113015-pattern-role-developer.md
```

Pipeline:

1. Parse front-matter and body.
2. Sanitize through the existing Ship feedback sanitizer (strips accidental paths, email-looking strings, and git remote URLs).
3. Build a `feedback.submit` telemetry event (if `telemetry.share=true` and `scope.improvement_drafts=true`).
4. `POST /feedback` with `{ artifact: {kind,id,version}, title, summary, suggestion, context, stack }`.
5. Server creates a GitHub issue on the Ship repository labeled:
   ```
   feedback
   artifact:<id>
   version:<version>
   ```
6. `shipctl` prints the issue URL and moves the draft into `.ship/feedback-drafts/sent/`.

Failure modes:

- Sanitizer rejects the content: `shipctl` prints the reason, does not send, leaves the draft in place.
- Server `5xx`: retried once with backoff, then kept as a local draft with a message to retry.
- `telemetry.share=false`: feedback still submits (it is an explicit action), but no `feedback.submit` telemetry event is emitted.

## Commands

| Command                                                                                 | Behavior                                                                 |
|-----------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `shipctl feedback draft --kind <k> --id <id> --version <v> [--title ...] [--summary ...]` | Creates a draft file, opens it in `$EDITOR` when interactive.            |
| `shipctl feedback list`                                                                 | Lists drafts in `.ship/feedback-drafts/`, split by sent / unsent.        |
| `shipctl feedback show <draft-file>`                                                    | Renders the draft to stdout for review.                                  |
| `shipctl feedback submit <draft-file>`                                                  | Submits the draft following the pipeline above.                          |

Examples:

```bash
shipctl feedback draft --kind pattern --id role-developer --version 1.4.2 \
  --title "Missing mobile preview step" \
  --summary "Evidence checklist misses mobile preview"

shipctl feedback list
shipctl feedback submit .ship/feedback-drafts/2026-04-17-113015-pattern-role-developer.md
```

## Privacy

- `anonymous_id` is a UUID v4 generated by `shipctl init` and stored in `.ship/config.yml:telemetry.anonymous_id`.
- The id is the only stable identifier Ship ever sees. It is not derived from the user, machine, or repository.
- `shipctl telemetry reset-id` replaces the id and invalidates the old one locally. The server treats the old id as a different adopter; there is no linking table.
- No identity or billing data is ever associated server-side.

## Agent responsibility

Agents running inside the adopter's environment (Cursor, Codex, Claude, others) have a narrow set of obligations when they interact with telemetry and feedback:

- When an agent consumes an artifact, it MAY call `shipctl <kind> show <id> --used-by <agent>` so that the `artifact.use` event carries the agent name.
- At the end of a session, if the agent detects a gap between the artifact and the actual stack (e.g. the pattern assumes Linear but the repo uses Jira) or a concrete improvement, it MAY draft feedback with `shipctl feedback draft`.
- The agent MUST NOT auto-submit feedback. Submission is always explicitly initiated by a human via `shipctl feedback submit <draft>`.
- The agent MUST NOT toggle `telemetry.share`. Only the human user (via `shipctl telemetry on|off` or editing the config) can change that switch.

## Event schema reference

All events share a common envelope:

```json
{
  "type": "<name>",
  "ts": "<ISO 8601 UTC>",
  "anonymous_id": "<UUID v4>",
  "shipctl_version": "<semver>",
  "stack_preset": "<preset or null>",
  "payload": { /* event-specific */ }
}
```

Per-event payload shapes:

### `artifact.fetch`

```json
{
  "kind": "pattern|tool|collection|doc",
  "id": "<slug>",
  "version": "<semver>",
  "source": "cache|network",
  "ttl_age_h": <number>
}
```

### `artifact.use`

```json
{
  "kind": "pattern|tool|collection|doc",
  "id": "<slug>",
  "version": "<semver>",
  "agent": "cursor|codex|claude|..."
}
```

### `artifact.sync`

```json
{
  "categories": ["pattern", "tool", "collection"],
  "updates_count": <int>,
  "failures_count": <int>
}
```

### `feedback.submit`

```json
{
  "artifact": { "kind": "pattern", "id": "role-developer", "version": "1.4.2" },
  "summary": "<one-line>",
  "suggestion": "<one-paragraph>",
  "stack": { "tracker": "linear", "ci": "gh-actions", "agents": ["cursor"], "preset": "web-app" }
}
```

### `doctor.result`

```json
{
  "stack": { "tracker": "linear", "ci": "gh-actions", "agents": ["cursor"], "preset": "web-app" },
  "findings_hash": "<hex sha-256>"
}
```

`findings_hash` is the SHA-256 of a canonicalized list of finding names — never the textual details of failures, which could leak paths or code.

## Sample round trip

Cursor kicks off a developer run inside a web-app preset. The session fetches and uses one pattern, syncs, and the user files feedback. With `telemetry.share=true` and `scope.improvement_drafts=true`, the outbox grows:

```
{ "type": "artifact.fetch", ... "payload": { "kind":"pattern", "id":"role-developer", "version":"1.4.2", "source":"network", "ttl_age_h":0 } }
{ "type": "artifact.use",   ... "payload": { "kind":"pattern", "id":"role-developer", "version":"1.4.2", "agent":"cursor" } }
{ "type": "artifact.sync",  ... "payload": { "categories":["pattern","tool"], "updates_count":2, "failures_count":0 } }
{ "type": "feedback.submit",... "payload": { "artifact":{"kind":"pattern","id":"role-developer","version":"1.4.2"}, "summary":"...", "suggestion":"...", "stack":{ ... } } }
```

All four ship in one `POST /telemetry` batch at the next command boundary.

## Feedback API details

### `POST /feedback`

Request:

```json
{
  "artifact": { "kind": "pattern", "id": "role-developer", "version": "1.4.2" },
  "title": "Developer PR checklist misses mobile preview step",
  "summary": "...",
  "suggestion": "...",
  "context": "free-form markdown",
  "stack": { "tracker": "linear", "ci": "gh-actions", "agents": ["cursor"], "preset": "web-app" },
  "anonymous_id": "b4a7...-v4"
}
```

Response:

```json
{
  "issue_url": "https://github.com/elmundi/ship/issues/1234",
  "issue_number": 1234,
  "labels": ["feedback", "artifact:pattern:role-developer", "version:1.4.2"],
  "deduplicated": false
}
```

### Deduplication

Before opening a new issue the server checks for existing open issues filtered by the labels:

```
artifact:<kind>:<id>
version:<version>
```

If a match exists:

- The server posts the new submission as a comment on the existing issue.
- The HTTP response uses the existing `issue_url` and `issue_number` and sets `deduplicated: true`.
- No new GitHub issue is created.

`shipctl` reports `deduplicated → existing #<n>` in the CLI output and still moves the local draft into `feedback-drafts/sent/`. The `feedback.submit` telemetry event is emitted in both cases (its `artifact` payload field is the same; the server-side dedup is invisible to the event schema).

### Sanitizer rules

Before the server opens an issue it runs the existing Ship feedback sanitizer over `summary`, `suggestion`, and `context`:

- Strips strings matching common path patterns inside the adopter's repo (e.g. anything resembling `/Users/…`, `/home/…`, `C:\\…`).
- Strips email addresses and replaces them with `<redacted-email>`.
- Strips git remote URLs (`git@*`, `https://github.com/*`, `https://gitlab.com/*`, …) and replaces them with `<redacted-remote>`.
- Strips secrets that match well-known token prefixes (GitHub PAT, Linear API key, etc.).

The sanitizer runs post-submission and before issue creation. If it strips any pattern, the server annotates the created issue with a note:

```
> Note: redacted 2 path-like strings and 1 git remote URL from this submission.
```

Client-side sanitizing is out of scope; the server is authoritative.

### Issue shape

The GitHub issue body is assembled server-side:

```markdown
## Artifact
pattern:role-developer@1.4.2

## Stack
tracker: linear
ci: gh-actions
agents: cursor
preset: web-app

## Summary
<sanitized summary>

## Suggestion
<sanitized suggestion>

## Context
<sanitized context>

---
Submitted via shipctl (anonymous_id redacted).
```

`anonymous_id` is never exposed on the issue. The mapping id ↔ issue lives server-side only and is discarded at the retention boundary.

## CI usage

Ship expects `shipctl` to run in CI both as a validator (`shipctl doctor`) and as a consumer (`shipctl pattern fetch ...`). Telemetry in CI follows the same rules with these refinements:

- CI runs SHOULD set `SHIP_TELEMETRY=false` by default unless the organization explicitly opts in.
- `artifact.fetch` events generated by CI carry `source=network` the first time a runner starts with an empty cache, then `source=cache` for subsequent steps. The envelope tracks `shipctl_version` but nothing that identifies the runner.
- `feedback.*` events are never emitted by CI. Feedback is a human workflow.

## Opt-out robustness

`telemetry.share=false` is the master switch. Additionally:

- Setting `SHIP_TELEMETRY=false` environment-wide overrides a `telemetry.share=true` in config; this lets operators disable telemetry fleet-wide without editing configs.
- `shipctl telemetry off` flushes the outbox once, then deletes it. Subsequent runs do not recreate the outbox.
- `shipctl telemetry reset-id` rotates `anonymous_id`. The previous id is not sent to the server; the rotation is purely local.
- The `POST /telemetry` endpoint returns `204 No Content` for payloads containing only opted-out events; `shipctl` treats this as success and clears the outbox entries.

## Open questions

- **Retention period.** How long does the server keep `telemetry_events`? Current working assumption: 180 days hot, aggregated summaries kept longer. Needs explicit policy.
- **Export / delete-my-data.** Should `shipctl telemetry export` pull back all events for the current `anonymous_id`? Should `shipctl telemetry delete` request server-side deletion?
- **Aggregation layer.** Where do we compute "top 20 artifacts by usage per preset" — on query or in nightly materialized views?
- **Rate limit envelope.** Is 60/min per `anonymous_id` enough for a busy monorepo that does many `artifact.fetch` in CI? Alternative: higher limit for events coming from `source=cache`.
- **Proxy support.** `shipctl` respects `HTTPS_PROXY`/`HTTP_PROXY` for fetch today. Should telemetry explicitly go through a different proxy (e.g. `SHIP_TELEMETRY_PROXY`) so operators can block it independently?
- **Feedback upvotes.** Should subsequent adopters be able to submit a "me too" upvote (an additional `feedback.upvote` event) referencing an existing issue, rather than opening duplicates?

## Changelog

- 2026-04-17: Initial draft.
- 2026-04-17: Renamed event field `event` → `type` in all examples to match implementation; clarified rate limit (60 batches/min per `anonymous_id`, not per event); documented `POST /telemetry` `202 {accepted, rejected, reasons}` response; required single-`anonymous_id` batches with `400` on mixed ids; promoted server-side payload-key denylist (`path, code, diff, branch, remote, email`) and feedback deduplication (existing-issue match by `artifact:<kind>:<id>` + `version:<v>` labels → comment + `deduplicated: true`) from open questions into the main body.
