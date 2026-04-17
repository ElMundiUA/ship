---
rfc: 0001
title: "Artifacts protocol"
status: Accepted
created: 2026-04-17
---

# RFC-0001 — Artifacts protocol

## Summary

Ship artifacts — patterns, tools, workflows, collections, and documentation — are versioned units served exclusively by the Ship site over HTTP and cached locally by `shipctl`. Clients never vendor methodology into their own git history; every consumption is an explicit, versioned read from the single source of truth. The protocol below defines the artifact shape, the HTTP surface used to list and fetch them, the local cache layout, and the rules agents follow when they consume an artifact.

## Motivation

Methodology drifts the moment it is forked. A tracker template, a release gate, or a role prompt that is copied into ten client repositories becomes ten slightly different artifacts within a quarter. Ship fixes this with a single distribution mechanism:

- Methodology lives in one place: the Ship repository, published via the Ship site.
- Clients read from that site through `shipctl` and cache results locally.
- Every artifact carries a semver version and a content hash; a client on a different release will produce a different hash and will know it is drifting.
- Updates are a one-line `shipctl sync`, not a multi-file merge.

The artifacts protocol is the contract that makes this workable across agents, CI, and humans.

## Artifact kinds

Ship distributes five kinds of artifacts. Any new type must extend this list in a follow-up RFC, not be introduced ad hoc.

| Kind         | Purpose                                                              | Typical `path`                                   |
|--------------|----------------------------------------------------------------------|--------------------------------------------------|
| `pattern`    | Instruction slice for a role or lane (prompt-shaped).                | `prompts/**/*.md`                                |
| `tool`       | Integration or adapter description (CI, tracker, scanner, agent).    | `tools/**/*.md`                                  |
| `workflow`   | End-to-end runbook connecting multiple roles or tools.               | `workflows/**/*.md`                              |
| `collection` | Curated bundle of artifacts for a stack, preset, or vertical.        | `collections/**/*.md`                            |
| `doc`        | Any other markdown under `documentation/` referenced by the site.    | `documentation/**/*.md`                          |

`doc` is the fallback: anything under `documentation/` that the site indexes becomes fetchable as a `doc` artifact with its path as the identifier (stable path = stable id).

## Manifest entry schema

Every artifact kind has a `manifest.json` at the root of its directory listing the entries. All catalog endpoints (`GET /manifest`, `GET /<kind>s`) project the same record shape for list responses.

Required fields per entry:

| Field            | Type      | Notes                                                                                         |
|------------------|-----------|-----------------------------------------------------------------------------------------------|
| `id`             | string    | Stable slug, unique within kind.                                                              |
| `title`          | string    | Human title.                                                                                  |
| `summary`        | string    | One-line description.                                                                         |
| `path`           | string    | Repo-relative path to the markdown body.                                                      |
| `tags`           | string[]  | Flat tag list.                                                                                |
| `group`          | string    | Logical grouping (`onboarding`, `cloud-agent`, `release`, etc.).                              |
| `version`        | string    | Semver `MAJOR.MINOR.PATCH`. Required, no `v` prefix.                                          |
| `content_sha256` | string    | Hex-encoded SHA-256 of the canonical content bytes. Computed server-side. Never hand-edited.  |
| `updated_at`     | string    | ISO 8601 UTC timestamp of the last publish for this version.                                  |
| `channel`        | enum      | `stable` or `edge`.                                                                           |
| `min_shipctl`    | string    | Minimum `shipctl` semver required to consume this artifact.                                   |
| `deprecated`     | bool      | `true` if the artifact is kept for reference but should not be adopted.                       |
| `replaced_by`    | string \| null | New `id` to migrate to when `deprecated=true`; otherwise `null`.                          |
| `yanked`         | bool      | `true` if the version is permanently withdrawn (broken / unsafe). `GET /<kind>s/{id}` for a yanked entry returns HTTP `410 Gone`. Default `false`. |

Example entry for the `cloud-developer` pattern (JSON, as returned by `GET /patterns/cloud-developer`):

```json
{
  "kind": "pattern",
  "id": "cloud-developer",
  "title": "Developer",
  "summary": "Implementation role: branch contract, PR shape, evidence.",
  "path": "prompts/cloud-agent/developer.md",
  "tags": ["implementation", "pr"],
  "group": "cloud-agent",
  "version": "1.4.2",
  "content_sha256": "9f1c0a...d7",
  "updated_at": "2026-04-17T09:21:08Z",
  "channel": "stable",
  "min_shipctl": "0.3.0",
  "deprecated": false,
  "replaced_by": null
}
```

## Version bump rules

Versioning is strict; the version is the primary signal agents use when they record "what methodology was I running against".

- **Any change to the rendered markdown body MUST increase the version.** This includes whitespace-affecting rewrites that change the semantic reading.
- **MAJOR** for breaking semantic changes — role boundaries moved, gate order inverted, contract renamed, artifact split/merged.
- **MINOR** for additive changes — new optional section, new tag, new evidence requirement that is compatible with earlier behavior.
- **PATCH** for clarifications — wording fixes, typo corrections, example tweaks, link updates that do not change the instruction semantics.

`content_sha256` is computed by the publisher pipeline over the canonical body (LF newlines, trailing newline, UTF-8, no BOM). It is never edited by hand. A mismatch between manifest hash and body is a publish-time failure.

Rationale: the hash lets clients detect cache corruption and lets feedback/telemetry attribute outcomes to an exact byte-identical artifact, even if two teams read the same semver at different moments in the release cycle.

### What counts as MAJOR

The following changes are MAJOR, not MINOR:

- Renaming or removing a lane, role, gate, or state transition the artifact defines.
- Changing required evidence in a way that existing PRs no longer satisfy the artifact.
- Reordering the SDLC grid or renaming a grid cell referenced by other artifacts.
- Splitting one artifact into two, or merging two into one (both sides produce MAJOR bumps on the affected ids).
- Changing the `path` of the artifact body (the id stays the same; the path is in the manifest, not the id).

### What counts as MINOR

- Adding a new optional evidence bullet that does not invalidate existing practice.
- Adding a new tag or group alias.
- Adding a new optional section that older consumers can ignore.

### What counts as PATCH

- Typo fixes, grammar, link updates.
- Rewording for clarity without changing instructions.
- Example refreshes (updated screenshots or sample commands).

## HTTP API surface

All endpoints live under the Ship site base (`SHIP_API_BASE`, for example `https://ship.elmundi.com/api/methodology`). Responses are JSON unless noted.

### `GET /manifest`

Flat list across all kinds. Designed for cheap freshness checks and cache diffing.

Response:

```json
[
  {
    "kind": "pattern",
    "id": "cloud-developer",
    "version": "1.4.2",
    "content_sha256": "9f1c0a...d7",
    "updated_at": "2026-04-17T09:21:08Z",
    "deprecated": false,
    "channel": "stable"
  },
  {
    "kind": "workflow",
    "id": "scheduled-sdlc-lane",
    "version": "2.1.0",
    "content_sha256": "71b3...aa",
    "updated_at": "2026-04-10T11:02:50Z",
    "deprecated": false,
    "channel": "stable"
  }
]
```

### `GET /<kind>s`

Existing list endpoints (`/patterns`, `/tools`, `/workflows`, `/collections`) keep their current shape and gain the version fields above (`version`, `content_sha256`, `updated_at`, `channel`, `min_shipctl`, `deprecated`, `replaced_by`). `doc` uses `GET /docs` with the path as the id.

### `GET /<kind>s/{id}`

Returns the full entry plus the markdown `content` string.

- Default: latest non-deprecated version on the requested `channel` (or current default channel).
- `?version=X.Y.Z`: returns that exact version verbatim. Returns `404` with `{"error": "version_not_found"}` if the version does not exist for the id.
- `?channel=edge`: selects edge channel for the default lookup.

### `GET /<kind>s/{id}/versions`

Version index for a single artifact.

```json
[
  { "version": "1.4.2", "updated_at": "2026-04-17T09:21:08Z", "changelog": "Clarify PR evidence bullet." },
  { "version": "1.4.1", "updated_at": "2026-03-22T14:07:11Z", "changelog": "Typo fixes." },
  { "version": "1.4.0", "updated_at": "2026-03-10T08:40:01Z" }
]
```

`changelog` is optional and free-form; when absent, clients should fall back to the git log of the artifact file.

### `POST /fetch`

The existing fetch endpoint remains the canonical "give me a body by either path or kind+id" entry point. Payloads:

```json
{ "path": "prompts/cloud-agent/developer.md" }
```

```json
{ "kind": "pattern", "id": "cloud-developer" }
```

Optional: `"version": "1.4.2"` pins the exact version. Without `version`, the latest on the configured channel is returned. The response includes the same version fields plus `content`.

## Local cache layout

`shipctl` keeps a single cache directory per repository. Everything is content-addressable by `<kind>/<id>@<version>`.

```
.ship/cache/
  manifest.json                        # mirror of GET /manifest + fetched_at
  <kind>/<id>@<version>.md             # canonical body bytes
  <kind>/<id>@<version>.meta.json      # {content_sha256, updated_at, source_url, fetched_at}
```

`manifest.json` on disk adds one extra top-level field:

```json
{
  "fetched_at": "2026-04-17T10:00:00Z",
  "entries": [ /* same shape as GET /manifest */ ]
}
```

The cache never stores anything the server did not return. `shipctl doctor` verifies that every `.meta.json` matches the file it annotates.

## Fetch policy

On `shipctl <kind> fetch <id>`, and when any higher-level command resolves an artifact:

1. Look up `<kind>/<id>@<resolved_version>` in cache.
2. If absent → HTTP `POST /fetch` → write body and `.meta.json` → serve.
3. If present and `now - fetched_at ≤ api.ttl_hours` → serve from cache.
4. If present and `now - fetched_at > api.ttl_hours` → cheap compare against `/manifest`:
   - If `content_sha256` and `version` are unchanged → update `fetched_at` in `.meta.json`, serve from cache.
   - If either changed → conditional `POST /fetch`, overwrite cache, serve.

Failure modes:

- Network failure with a cached copy and `api.offline_ok=true`: serve cached copy, emit a warning, do not fail the command.
- Network failure without a cached copy: exit non-zero with a clear error naming the missing `<kind>/<id>`.
- `content_sha256` mismatch after fetch: refuse to write cache, exit non-zero, ask the user to rerun; never degrade silently.

## Pinning

A project can pin individual artifacts or kinds via `.ship/config.yml`:

```yaml
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
    workflow/scheduled-sdlc-lane: "~2.1"
    collection/web-application: "^3.0.0"
  auto_update: true
```

Rules:

- Pins accept an exact version (`1.4.2`), a caret range (`^1.4.0`), a tilde range (`~1.4`), or a bare major (`1`).
- `shipctl sync` never upgrades a pinned entry. It fetches the highest version satisfying the pin.
- `shipctl sync --force-unpin <kind>/<id>` temporarily ignores the pin for one run and prints a warning.
- A pin that cannot be satisfied (no published version in range) is a hard error.

Pinning is how clients opt out of continuous methodology updates for controlled-change environments (pharma, fin, regulated deploys).

## Channels

Two channels, with room for future additions:

- `stable`: default. Only versions the Ship project has marked as released.
- `edge`: pre-release content. Opt-in via `api.channel: edge` in `.ship/config.yml` or `SHIP_CHANNEL=edge`.

An artifact may exist on both channels with different version timelines. Clients on `stable` never see `edge`-only versions; clients on `edge` see both, preferring the highest semver overall.

## Agent protocol (reference)

Agents that consume Ship artifacts must follow this short contract. It is the same contract whether the agent runs in Cursor, Codex, Claude Code, or a CI job.

1. **Resolve before use.** Before applying any artifact, the agent MUST call `shipctl <kind> show <id>` (or the equivalent CLI). This is local-only when the cache is warm; `shipctl` will auto-fetch if the artifact is missing or stale.
2. **Record the exact version.** The consumed version MUST be written into the PR description or ticket comment, in the form `<kind>:<id>@<version>` (example: `pattern:cloud-developer@1.4.2`). Multiple artifacts are listed on separate lines. This is the evidence trail used by retros and telemetry.
3. **Do not copy bodies into the repo.** If the agent needs a snippet in-line (for example, a checklist in a PR template), the snippet must reference the artifact id and version next to it, and must not be edited away from the source.
4. **Feedback is opt-in.** After a session, if `telemetry.share=true`, the agent MAY draft a feedback suggestion with `shipctl feedback draft`. It MUST NOT submit feedback without explicit user consent (see RFC-0003).

## Deprecation path

An artifact marked `deprecated=true` keeps serving its last published body for existing clients but produces a warning on fetch:

```
warning: pattern:cloud-developer@1.4.2 is deprecated; replaced_by=cloud-developer-v2
         see GET /patterns/cloud-developer-v2
```

### Response shapes

Deprecation and yank are distinct operations with distinct HTTP responses:

- **Deprecated (`deprecated=true`).** `GET /<kind>s/{id}` and `POST /fetch` return HTTP `200 OK` with the body still attached and a top-level JSON field
  ```json
  "deprecation_notice": "replaced_by=<id>"
  ```
  in addition to the regular entry fields. Clients are expected to render a warning but otherwise serve the body.
- **Yanked (`yanked=true`).** `GET /<kind>s/{id}` (and `POST /fetch` when targeting a yanked version) returns HTTP `410 Gone` with body
  ```json
  { "error": "yanked", "kind": "...", "id": "...", "version": "...", "replaced_by": "<id-or-null>" }
  ```
  No artifact body is returned. `shipctl` reports `yanked: <count>` in `sync` summaries and refuses to install a yanked version even with `--force-unpin`.

Rules:

- Deprecated artifacts remain fetchable for at least one Ship release after their `updated_at`.
- `replaced_by` must point to a non-deprecated, non-yanked artifact of the same kind.
- `shipctl sync` auto-updates pins whose target is deprecated only when `--accept-replacement` is set, and prints the old → new pair for review.
- Yanked versions are removed from the latest-version resolution immediately; clients pinned to a yanked version see HTTP `410` until they bump the pin.

## Security

- **Content type.** Bodies are served as `text/markdown; charset=utf-8`. No other media types are served by the artifacts protocol.
- **Sanitizer.** Feedback and telemetry already use Ship's existing sanitizer for payloads flowing back to the server; the sanitizer is not applied to artifact bodies going out to the client (they are authored content, not user data).
- **Checksum.** Every cache write verifies `content_sha256` against the manifest entry. A mismatch aborts the write.
- **Transport.** HTTPS only in production. Plain HTTP is allowed only against `localhost`/`127.0.0.1` for local development.
- **No credentials.** The artifacts protocol is read-only and unauthenticated in its default deployment. Private instances may front it with a reverse proxy; `shipctl` passes `Authorization` headers through from `SHIP_API_TOKEN` when set, but never persists them.

## Examples

### Resolving a pattern from cold cache

A new repository with no `.ship/cache/` runs:

```bash
shipctl pattern show cloud-developer
```

Sequence:

1. `shipctl` loads `.ship/config.yml` → `api.base_url`, `api.channel`, `artifacts.pins`.
2. No cache exists → skip freshness check.
3. `POST /fetch` with `{ "kind": "pattern", "id": "cloud-developer" }` (no `version` because no pin).
4. Server returns entry `cloud-developer@1.4.2` + body.
5. `shipctl` verifies `content_sha256` of the returned body.
6. On match, writes `.ship/cache/pattern/cloud-developer@1.4.2.md` and `.ship/cache/pattern/cloud-developer@1.4.2.meta.json`.
7. Prints the body to stdout.

### Resolving a pinned pattern with warm cache

```yaml
artifacts:
  pins:
    pattern/cloud-developer: "1.4.2"
```

```bash
shipctl pattern show cloud-developer
```

Sequence:

1. Resolve pin → require `pattern/cloud-developer@1.4.2` exactly.
2. Cache hit → read body and `.meta.json`.
3. `fetched_at` is 2 hours old, `api.ttl_hours=24` → serve from cache without any network.

### Resolving a pinned pattern past TTL

Same pin, but `fetched_at` is 30 hours old.

1. `age > ttl_hours` → cheap compare via `/manifest`.
2. Manifest reports `pattern/cloud-developer@1.4.2` with unchanged `content_sha256`.
3. Update `fetched_at` on the `.meta.json`, serve cache.

If the server had already published `1.4.3` but the pin forces `1.4.2`, the manifest compare still finds `1.4.2` (the pinned version) identical and nothing upgrades. The next time the user runs `shipctl sync --dry-run`, they see:

```
pattern/cloud-developer: pinned 1.4.2 (available: 1.4.3)
```

### Publishing a new version

Author edits `prompts/cloud-agent/developer.md` and opens a PR on the Ship repo:

1. CI runs the manifest linter.
2. Linter sees the body changed and the `version` field is still `1.4.2`.
3. Build fails: "body changed but version unchanged; bump at least PATCH."
4. Author bumps to `1.4.3` with a short `changelog:`.
5. CI recomputes `content_sha256`, commits it back, and publishes.

`content_sha256` drift is fatal at publish time; clients should never see a mismatched hash.

## Interaction with other RFCs

- **RFC-0002** (`.ship/config.yml`): `api.*` and `artifacts.pins.*` drive every decision the artifacts protocol makes client-side.
- **RFC-0003** (telemetry): `artifact.fetch`, `artifact.use`, and `artifact.sync` events reference the exact `kind:id@version` resolved here.
- **RFC-0004** (adapters): adapters are themselves artifacts of kind `tool` and `collection`. The protocol applies to them unchanged.

## Sync command (reference)

`shipctl sync` is the batch operation that reconciles the local cache with the server manifest.

```
shipctl sync [--dry-run] [--force-unpin <kind>/<id>] [--accept-replacement]
```

Algorithm:

1. `GET /manifest` → remote inventory.
2. For each locally cached `<kind>/<id>@<version>`:
   - If entry is pinned and the pin is still satisfied → keep.
   - If entry is pinned and the pin cannot be satisfied → error.
   - If entry is unpinned and a newer version exists → plan upgrade.
   - If entry is `deprecated=true` with a `replaced_by` → plan replacement only when `--accept-replacement` is set.
3. Print plan. With `--dry-run`, stop here.
4. Execute plan: fetch new versions, verify hashes, update cache.
5. Emit `artifact.sync` telemetry event (see RFC-0003).

Sync is safe to interrupt: it never deletes a cache entry before a replacement has been verified.

## Error taxonomy

`shipctl` commands that touch the artifacts protocol use these exit codes. Tooling may rely on them.

| Code | Meaning                                                |
|------|--------------------------------------------------------|
| `0`  | Success.                                               |
| `10` | Config invalid.                                        |
| `11` | Artifact not found (id or version).                    |
| `12` | Content hash mismatch after fetch.                     |
| `13` | Pin cannot be satisfied.                               |
| `14` | Deprecated artifact without `--accept-replacement`.    |
| `15` | `min_shipctl` not met.                                 |
| `20` | Network failure and no cached copy.                    |
| `21` | Network failure with cached copy and `offline_ok=false`. |
| `30` | Cache corruption (detected by doctor).                 |

## Open questions

- **Signed artifacts.** Should Ship sign artifacts (minisign / cosign) so that offline verification is possible even when the cache is synced between machines?
- **Partial fetch for large collections.** Collections can aggregate many bodies; do we want a streaming or chunked fetch for very large bundles, or should `shipctl` always expand them artifact by artifact?
- **Offline bootstrap bundle.** A periodic `ship-release.zip` containing a full manifest snapshot plus all referenced bodies would unblock fully air-gapped adoption. What is the cadence and what is the signing story?
- **Channel promotion policy.** How are `edge` → `stable` promotions decided? Manual release notes, automated after N days, or a mix?
- **Hash scope for `doc` artifacts.** `doc` paths under `documentation/` can include rendered anchors and images. Does the canonical hash cover raw markdown only, or the rendered HTML? Current draft: raw markdown only.
- **Multi-tenant private mirrors.** Some organizations will mirror the Ship site internally. Do we standardize a "mirror manifest" endpoint so `shipctl sync` can be pointed at a mirror without config gymnastics?

## Changelog

- 2026-04-17: Initial draft.
- 2026-04-17: Added `yanked` field; clarified 410/200 response shapes for yank/deprecate.
