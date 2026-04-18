---
artifact_kind: tool
id: chroma-local
name: Chroma (local)
version: 1.0.0
channel: stable
min_shipctl: 0.3.0
updated_at: "2026-04-16T09:17:12+03:00"
content_sha256: cf6906d8668092a1e82096b7bd1863d36379583f72a351399e9a2db499b390d9
deprecated: false
replaced_by: null
yanked: false
group: platform
tags: [embeddings, local, index]
authors: [@elmundi/ship-core]
license: Apache-2.0
description: >-
  On-disk vector index for POST /search over docs and prompts. Use when integrating this surface into a Ship setup, when evaluating vendor neutrality for a procurement, or when an adapter under platform needs to call into it.
spec:
  capability: platform
  install_target: documentation/tools/integrations/chroma-local.md
---

# Chroma (local vector index)

**Role in Ship:** powers **`POST /search`** in the bundled FastAPI service — embeddings over `documentation/`, `prompts/`, and `README.md`, persisted under `backend/.chroma/`.

## Operator notes

- Requires **`OPENAI_API_KEY`** on the server for embedding generation; index rebuilds when content fingerprints change (or when `FORCE_REINDEX=true`).
- Not a multi-tenant hosted vector DB — **local-first** for methodology search on a laptop or single VM.

## Read next

- [Backend API](/docs/tools/backend-api) — `/search`, `/fetch`, `/feedback`, `/patterns` (from the Ship repo use `npm run ship -- docs …` and `npm run ship -- patterns …`).
- Ship CLI: `ship search …` from the repo root (see root `package.json` script `ship`).
