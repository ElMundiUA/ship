# Ship

**Ship** is governed SDLC automation: tracker as system of record, deterministic picks, one delivery role per window, versioned prompts in git, and an audit trail that survives the first incident review.

This repository (**[ElMundiUA/ship](https://github.com/ElMundiUA/ship)**) is the **standalone** framework package (manual + Node CLI + `cloud-prompts/` + `scripts/`). It was extracted from the ElMundi monorepo path `tools/linear-agent/`; **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)** may keep that path as a **mirror** until it switches to a submodule or version pin of **ship**.

**Docs (live):** [ship.elmundi.com](https://ship.elmundi.com) when deployed.  
**Package / docs version:** `1.0.0` — keep in sync with `mkdocs.yml` → `extra.doc_version` and `docs/stylesheets/extra.css` header chip.

## Documentation (MkDocs)

```bash
git clone https://github.com/ElMundiUA/ship.git
cd ship
python3 -m venv .venv-docs
source .venv-docs/bin/activate   # Windows: .venv-docs\Scripts\activate
pip install -r requirements-docs.txt
mkdocs serve
```

- Static build: `mkdocs build` → `site/`. Top nav sections are **one long page** each.
- **PDF:** browser **Print → Save as PDF** (see `docs/pdf-export.md`).
- **Diagrams:** `docs/diagrams/*.d2`; SVG regenerates on build if `d2` is on `PATH` (`hooks/d2_prebuild.py`).

## Node CLI

```bash
npm install
npm run build   # when TypeScript under `src/` changes
```

Local secrets: `.env` at this repo root (never commit).

## Docs deployment (Docker + Bunny)

The manual is built into a static site and served by **nginx** on port **8080**, with **`GET /health`** returning `200` for Magic Containers probes.

**Local image:**

```bash
docker build -t ship-docs:local .
docker run --rm -p 8080:8080 ship-docs:local
# open http://127.0.0.1:8080/  — health: http://127.0.0.1:8080/health
```

**CI — production:** [`.github/workflows/docker-publish-bunny.yml`](.github/workflows/docker-publish-bunny.yml) (**Ship — Docker Hub + Bunny deploy**) — on every push to `main`: build, push to Docker Hub, ensure Magic Container app ([`scripts/bunny-ship-docs.mjs`](scripts/bunny-ship-docs.mjs)), update image tag, then **`POST /mc/apps/{id}/deploy`** so pods actually roll.

**CI — PRs only:** [`.github/workflows/docs.yml`](.github/workflows/docs.yml) (**Ship docs (PR)**) runs `mkdocs build` on pull requests — **no Bunny**, no Docker Hub. If you only see this workflow green on `main`, you are looking at an old run; on `main` you want **Ship — Docker Hub + Bunny deploy**.

**GitHub → repository secrets (Actions):**

| Secret | Purpose |
|--------|---------|
| `DOCKER_HUB_TOKEN` | Push image (Docker Hub access token) |
| `BUNNY_MAIN_API_KEY` | Bunny **account** API key (Magic Containers; same header as `AccessKey` on `api.bunny.net/mc`) |

**Repository variables (optional):**

| Variable | Purpose |
|----------|---------|
| `DOCKER_IMAGE_NAME` | Default `dekus/ship-docs` |
| `DOCKERHUB_USERNAME` | Default `dekus` |
| `BUNNY_APP_ID` | Magic Container **application id** (UUID). If `POST …/mc/apps` returns **500** from Bunny’s side, create the app once in **Dashboard → Magic Containers** (name must match `SHIP_MC_APP_NAME`, default `ship-docs`) **or** paste its id here — the script then skips create and only deploys/updates image. |
| `BUNNY_CONTAINER_NAME` | Container template name inside the app — default **`ship`** (must match workflow + script) |
| `SHIP_MC_APP_NAME` | Magic Container app name — default **`ship-docs`** (ASCII, no spaces) |
| `BUNNY_REGION_IDS` | Optional — comma regions for `allowedRegionIds` (default `DE,UK,US`) |

**Troubleshooting — `Create app 500` on first deploy:** the workflow uses a minimal, OpenAPI-shaped body; repeated **HTTP 500** on `POST https://api.bunny.net/mc/apps` is a **Bunny platform/account** response (billing, Magic Containers entitlement, or transient outage), not a bad payload from this repo. **Workaround:** create the app manually with display name **`ship-docs`** (or set **`BUNNY_APP_ID`**), then push again — listing apps works without creating.

**DNS for `ship.elmundi.com`:** after a green deploy, open the workflow **summary** on GitHub — it lists the default Bunny host and CNAME steps. In short: in Bunny, add **custom hostname** `ship.elmundi.com` on the app endpoint; at DNS, **`CNAME` `ship` → target Bunny shows** (use their exact value, not guessed).

You can attach a GitHub **Environment** (e.g. `ship-docs`) for approval gates; if you do, **copy the same secrets** into that environment (repo-level secrets are not inherited).

## GitHub Actions (SDLC / Linear)

Workflow YAML in ElMundi’s monorepo lives under `.github/workflows/` and historically assumed checkout of `tools/linear-agent/`. When adopting **ship** as its own repo, set `working-directory` to **`.`** and use a normal full checkout. See **`examples/github-workflows/README.md`** for migration notes and links to the canonical files in **elmundi**.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). The ElMundi **product** monorepo remains mixed; only this package is meant to be uniformly permissive. See `docs/legal-copyright.md`.

© 2026 Denys Kuzin
