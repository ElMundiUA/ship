---
artifact_kind: tool
id: methodology-api
name: Methodology API
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-16T09:17:12+03:00"
content_sha256: 22d023482daf1e2666a4ed81ed9082ea93e44bd44d0c479d869fde086929a1a9
deprecated: false
replaced_by: null
yanked: false
group: platform
tags: [http, fastapi, agents]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  FastAPI for agents; humans use ship search, ship docs, and ship pattern|tool|… CLI against the same routes. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under platform needs to call into it.
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

The `**ship`** CLI uses the **same** FastAPI root for **`ship search`**, **`ship docs`** (documentation paths + feedback), and **`ship pattern` / `tool` / `workflow` / `collection`** when you are not inside a local Ship tree (default **`SHIP_API_BASE`**: deployed methodology URL, e.g. `https://ship.elmundi.com/api/methodology`; override with `--base-url`). Deploy this API publicly (or behind your reverse proxy) and point adopters at that single URL.

```bash
npm run ship -- search "intake idempotency" --top-k 6
npm run ship -- docs fetch documentation/adoption/delivery-quality-and-release-process.md
npm run ship -- search "release gates" --json
```

Run `npm run ship -- help` for all subcommands. Use `--json` in scripts for stable parsing.

## CLI (pattern, tool, workflow, collection)

- **Remote:** `GET /patterns`, `GET /tools`, … on the same `**SHIP_API_BASE`** as `POST /search`; catalog bodies can use **`POST /fetch`** with `{ "kind", "id" }` (**`ship pattern|tool|… fetch <id>`**).
- **Local tree:** run inside the Ship clone or set `**SHIP_REPO`** to read manifests from disk (no server).

```bash
npm run ship -- pattern list
npm run ship -- pattern show adopt-ship-generic
npm run ship -- pattern fetch adopt-ship-generic
npm run ship -- tool list
npm run ship -- tool show playwright
npm run ship -- workflow list
npm run ship -- workflow show pr-and-ci-gate
npm run ship -- collection list
npm run ship -- collection show web-application
```

## Endpoints (FastAPI)

Agents, CI, and other runtimes may call these directly; humans usually use the CLI above.

### `GET /patterns`

Returns metadata for every entry in `patterns/manifest.json` at the repository root (no file bodies).

Response shape:

```json
{
  "version": 1,
  "description": "...",
  "patterns": [
    {
      "id": "catalog-a1-intake",
      "title": "Structured intake",
      "summary": "...",
      "path": "prompts/catalog/A1-intake.md",
      "tags": ["intake", "labels"],
      "group": "lanes"
    }
  ]
}
```

### `GET /patterns/{pattern_id}`

Returns the same fields as one list item, plus a `content` string with the full markdown file (path must match the manifest and stay inside the repo).

Example (same as `npm run ship -- pattern show catalog-a1-intake --json` without `--json`):

```bash
curl -sS "http://127.0.0.1:8100/patterns/catalog-a1-intake"
```

### `GET /tools`

Returns full `**tools/manifest.json**` (same as CLI `ship tool list --json`).

### `GET /tools/{id}`

Returns one catalog entry plus markdown `content` for the manifest `path`.

### `GET /workflows` / `GET /workflows/{id}`

Same as tools for `**workflows/manifest.json**`.

### `GET /collections` / `GET /collections/{id}`

Same for `**collections/manifest.json**`.

### `POST /search`

Vector search over:

- `documentation/**/*.md`
- `prompts/**/*.md`
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

`kind` is one of `pattern`, `tool`, `workflow`, `collection`. **CLI:** `ship docs fetch <path>` vs `ship pattern|tool|workflow|collection fetch <id>`.

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

## v0.3 additions

The v0.3 surface implements RFC-0001 (artifacts protocol) and RFC-0003
(telemetry and feedback). All existing endpoints remain backward-compatible;
list endpoints simply gain new fields on each entry.

### Per-entry version fields

Every entry in `GET /patterns`, `GET /tools`, `GET /workflows`, and
`GET /collections` now carries:

- `version` — semver `MAJOR.MINOR.PATCH`
- `content_sha256` — hex SHA-256 of the referenced markdown body
- `updated_at` — ISO-8601 UTC timestamp of the last publish
- `channel` — `stable` or `edge`
- `min_shipctl` — minimum `shipctl` semver required
- `deprecated` — boolean
- `replaced_by` — id of the replacement artifact or `null`
- `yanked` — boolean

Fields are stamped by `scripts/stamp_artifact_versions.py` and verified in CI by
`scripts/ship_artifact_check.py`.

### `?channel=` filter

`GET /patterns`, `/tools`, `/workflows`, `/collections`, and `/manifest` accept
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

### `GET /manifest`

Single flat inventory across all five kinds (`pattern`, `tool`, `workflow`,
`collection`, `doc`). Designed for cheap freshness checks.

```json
{
  "version": 1,
  "generated_at": "2026-04-17T10:00:00+00:00",
  "entries": [
    {
      "kind": "pattern",
      "id": "cloud-developer",
      "version": "1.0.0",
      "content_sha256": "...",
      "updated_at": "2026-04-12T04:11:35+03:00",
      "channel": "stable",
      "deprecated": false,
      "yanked": false,
      "path": "prompts/cloud-agent/developer.md"
    }
  ]
}
```

The `doc` kind is auto-discovered: every `.md` and `.txt` under
`documentation/` and `prompts/`, plus `README.md`. The `id` of a `doc` entry is
its repo-relative path.

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

