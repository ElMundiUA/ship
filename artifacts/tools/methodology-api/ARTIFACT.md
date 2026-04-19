---
artifact_kind: tool
id: methodology-api
name: Methodology API
version: 1.1.0
channel: stable
min_shipctl: 0.10.0
updated_at: "2026-04-19T03:00:00+03:00"
content_sha256: 2a26fd8e1c55b5e542b2f4d8c0e47682df934196a1187e8465c112aac4ae7d70
deprecated: false
replaced_by: null
yanked: false
group: platform
tags: [http, fastapi, agents]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  FastAPI for agents; humans use shipctl search, shipctl docs, and shipctl pattern|tool|… CLI against the same routes. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under platform needs to call into it.
spec:
  capability: platform
  install_target: documentation/tools/backend-api.md
---

# Backend API

Ship now includes a lightweight backend for agent workflows: semantic search, file fetch, retro feedback, and **catalog HTTP** (patterns, tools, workflows, collections).

## Purpose

- Give humans and agents semantic access to framework knowledge (`/search` + `/fetch`), stable **catalog list/detail** (`GET /patterns`, `GET /tools`, …), and full bodies for any repo-relative markdown path.
- Collect operational retro feedback into Ship backlog safely (`/feedback`).
- Stay local-first: no external vector database.

## CLI (all HTTP via one base URL)

The **`shipctl`** CLI uses the **same** FastAPI root for **`shipctl search`**, **`shipctl docs`** (documentation paths + feedback), and **`shipctl pattern` / `tool` / `workflow` / `collection`** when you are not inside a local Ship tree (default **`SHIP_API_BASE`**: deployed methodology URL, e.g. `https://ship.elmundi.com.ua/api/methodology`; override with `--base-url`). Deploy this API publicly (or behind your reverse proxy) and point adopters at that single URL.

```bash
shipctl search "intake idempotency" --top-k 6
shipctl docs fetch documentation/adoption/delivery-quality-and-release-process.md
shipctl search "release gates" --json
```

Run `shipctl help` for all subcommands (or `npm run shipctl -- help` from a clone of this repo). Use `--json` in scripts for stable parsing.

## CLI (pattern, tool, workflow, collection)

- **Remote:** `GET /patterns`, `GET /tools`, … on the same **`SHIP_API_BASE`** as `POST /search`; catalog bodies use **`POST /fetch`** with `{ "kind", "id" }` (**`shipctl pattern|tool|… fetch <id>`**).
- **Local tree:** run inside the Ship clone or set **`SHIP_REPO`** to scan `artifacts/<kind>/<id>/ARTIFACT.md` files directly off disk (no server, per RFC-0005).

```bash
shipctl pattern list
shipctl pattern show adopt-ship-generic
shipctl pattern fetch adopt-ship-generic
shipctl tool list
shipctl tool show playwright
shipctl workflow list
shipctl workflow show pr-and-ci-gate
shipctl collection list
shipctl collection show web-application
```

## Endpoints (FastAPI)

Agents, CI, and other runtimes may call these directly; humans usually use the CLI above.

### `GET /patterns`

Returns metadata for every artifact found by scanning `artifacts/patterns/<id>/ARTIFACT.md` (frontmatter only, no body).

Response shape:

```json
{
  "version": 1,
  "description": "...",
  "patterns": [
    {
      "id": "adopt-ship-generic",
      "title": "Adopt Ship (generic)",
      "summary": "...",
      "path": "artifacts/patterns/adopt-ship-generic/ARTIFACT.md",
      "version": "1.0.0",
      "channel": "stable",
      "tags": ["adoption"],
      "group": "adoption"
    }
  ]
}
```

### `GET /patterns/{pattern_id}`

Returns the same fields as one list item, plus a `content` string with the full `ARTIFACT.md` (frontmatter + body) so agents can re-validate the version/sha they cached locally.

Example (same as `shipctl pattern show adopt-ship-generic --json` without `--json`):

```bash
curl -sS "http://127.0.0.1:8100/patterns/adopt-ship-generic"
```

### `GET /tools`

Same shape as `/patterns`, scanning `artifacts/tools/<id>/ARTIFACT.md` (matches CLI `shipctl tool list --json`).

### `GET /tools/{id}`

Returns one catalog entry plus the full `ARTIFACT.md` content.

### `GET /workflows` / `GET /workflows/{id}`

Same as tools, against `artifacts/workflows/<id>/ARTIFACT.md`.

### `GET /collections` / `GET /collections/{id}`

Same for `artifacts/collections/<id>/ARTIFACT.md`.

### `POST /search`

Vector search over:

- `documentation/**/*.md`
- `artifacts/**/ARTIFACT.md`
- `README.md`

Uses local Chroma persistence (`backend/.chroma/`) and OpenAI embeddings.

Request:

```json
{
  "query": "qa automation handoff",
  "top_k": 8
}
```

Response returns chunk snippets, path, chunk index, and vector distance.

### `POST /fetch`

Returns full markdown/text content.

**Documentation file** (after search hits give a `path`):

```json
{
  "path": "documentation/adoption/delivery-quality-and-release-process.md"
}
```

**Catalog entry** (pattern, tool, workflow, collection):

```json
{
  "kind": "pattern",
  "id": "catalog-a1-intake"
}
```

`kind` is one of `pattern`, `tool`, `workflow`, `collection`. **CLI:** `shipctl docs fetch <path>` vs `shipctl pattern|tool|workflow|collection fetch <id>`.

### `POST /feedback`

Creates an issue in Ship repo from daily retro insights.

Before issue creation, payload is scanned and sanitized:

- emails,
- common API token/key formats,
- simple `password/token/secret` key-value patterns.

If sensitive fragments are detected, they are rewritten into generalized placeholders before sending to GitHub.

## Local run

```bash
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

## Required environment variables

- `OPENAI_API_KEY` for `/search`
- `GITHUB_TOKEN` for `/feedback`

Optional:

- `SHIP_FEEDBACK_REPO` (default: `ElMundiUA/ship`)
- `OPENAI_EMBED_MODEL` (default: `text-embedding-3-small`)
- `FORCE_REINDEX=true` (force rebuild of vector index)
- `SHIP_TELEMETRY_DIR` (default: `backend/telemetry`) — directory for the `events.jsonl` append-log

## Artifacts protocol surface

The catalog endpoints implement RFC-0001 (artifacts protocol) on top of the
RFC-0005 folder layout (`artifacts/<kind>/<id>/ARTIFACT.md`). Every artifact's
metadata lives in YAML frontmatter inside that single file — the server is a
thin filesystem scanner with no separate `manifest.json` to keep in sync.

### Per-entry version fields

Every entry in `GET /patterns`, `GET /tools`, `GET /workflows`, and
`GET /collections` carries:

- `version` — semver `MAJOR.MINOR.PATCH`
- `content_sha256` — hex SHA-256 of the artifact body, computed with the
  `content_sha256:` line value cleared (see `scripts/restamp_artifact_shas.py`
  for the canonical hashing rule).
- `updated_at` — ISO-8601 UTC timestamp of the last publish
- `channel` — `stable` or `edge`
- `min_shipctl` — minimum `shipctl` semver required
- `deprecated` — boolean
- `replaced_by` — id of the replacement artifact or `null`
- `yanked` — boolean

Fields are authored in frontmatter, verified in CI by
`scripts/ship_artifact_check.py`, and re-stamped (when content changes) by
`scripts/restamp_artifact_shas.py`.

### `?channel=` filter

`GET /patterns`, `/tools`, `/workflows`, `/collections` accept
`?channel=stable|edge`. Default is `stable`, which filters entries whose
`channel` is `stable`. `edge` returns every entry regardless of channel.

### `?version=` on detail endpoints and `/fetch`

- `GET /patterns/{id}?version=X.Y.Z` and the same shape for tools, workflows,
  collections.
- `POST /fetch` with `{"kind": "...", "id": "...", "version": "X.Y.Z"}`.

If the requested version matches the current manifest version the response is
returned verbatim. Any other version responds with HTTP `404` and the detail
`"unknown version; current is <v>"`. v1 of the server does not walk git
history for older bodies.

When the entry is `deprecated=true`, the response still returns `200` but adds
`"deprecation_notice": "replaced_by=<id>"`. When `yanked=true`, the endpoint
responds with `410 Gone` carrying the same notice.

### Aggregated catalog

The legacy `GET /manifest` endpoint was retired in v0.10 (RFC-0005). Clients
should fan out the four per-kind list endpoints in parallel and concatenate
the results — the CLI already does this in `cli/lib/http.mjs::fetchManifest`.

```text
GET /patterns
GET /tools
GET /workflows
GET /collections
```

Each entry has the same per-entry shape documented above; clients add a
`kind` field client-side.

### `GET /<kind>s/{id}/versions`

Returns the version index for a single artifact. v1 always returns exactly one
entry (the current manifest version):

```json
{
  "id": "cloud-developer",
  "versions": [
    {
      "version": "1.0.0",
      "updated_at": "2026-04-12T04:11:35+03:00",
      "channel": "stable",
      "deprecated": false,
      "yanked": false
    }
  ]
}
```

### `POST /feedback` (extended)

Request now accepts an optional `artifact` object:

```json
{
  "title": "Developer checklist missing mobile preview",
  "summary": "…",
  "recommendations": ["add mobile preview bullet"],
  "artifact": {
    "kind": "pattern",
    "id": "cloud-developer",
    "version": "1.0.0"
  }
}
```

When `artifact` is present the server:

1. Applies labels `feedback`, `retro`, `artifact:<kind>:<id>`,
   `version:<version>` to the resulting issue.
2. Embeds a machine-readable footer in the issue body:
   `<!-- ship-feedback-meta: {"kind":"pattern","id":"cloud-developer","version":"1.0.0"} -->`.
3. **Dedupes**: before creating a new issue the server queries open issues
   with the same `artifact:*` and `version:*` labels. If an open issue
   exists, the submission becomes a new comment on that issue and the
   response carries `"deduplicated": true` with the same `issue_url`.

Response:

```json
{
  "issue_url": "https://github.com/…/issues/42",
  "issue_number": 42,
  "labels": ["feedback", "retro", "artifact:pattern:cloud-developer", "version:1.0.0"],
  "redactions_applied": 0,
  "deduplicated": false
}
```

### `POST /telemetry`

Accepts a batch of events and appends them to `backend/telemetry/events.jsonl`
(configurable via `SHIP_TELEMETRY_DIR`). Each line is a self-contained JSON
event augmented with `received_at`.

```json
{
  "events": [
    {
      "type": "artifact.fetch",
      "anonymous_id": "11111111-2222-4333-8444-555555555555",
      "timestamp": "2026-04-17T10:05:13+00:00",
      "payload": {"kind": "pattern", "id": "cloud-developer", "version": "1.0.0"}
    }
  ]
}
```

Rules:

- `anonymous_id` must match the UUIDv4 pattern.
- `type` must be one of `artifact.fetch`, `artifact.use`, `artifact.sync`,
  `feedback.submit`, `doctor.result`.
- Up to **100 events per request**; larger batches respond with `400`.
- `payload` must not contain any key named `path`, `code`, `diff`, `branch`,
  `remote`, or `email` (recursive denylist). Any match responds with `400`.
- Rate limit: **60 requests / minute per `anonymous_id`**. Excess requests
  respond with `429`.
- Success responds with `202 Accepted` and
  `{"accepted": <n>, "rejected": <n>, "reasons": [...]}`. Events with an
  unknown `type` are counted as rejected (never written).

### `DELETE /telemetry/{anonymous_id}`

Removes every event in the JSONL log matching the supplied id. Requires the
header `X-Ship-Confirm: yes` as a safety acknowledgement; without it the
server responds `400`. Returns `{"deleted": <n>}`.

### `GET /telemetry/{anonymous_id}/export`

Returns `{"events": [...]}` — all events for the supplied id. Allows adopters
to export their telemetry contribution for review or before deletion.

