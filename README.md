# Ship

**Ship** is governed SDLC automation: tracker as system of record, deterministic picks, one delivery role per window, versioned prompts in git, and an audit trail that survives the first incident review.

This repository is the **standalone** framework package: MkDocs manual, Node CLI, `cloud-prompts/`, and `scripts/`. Fork or vendor it into your product repo; configure **Linear project IDs, team keys, domains, and secrets** via `.env` and CI variables — see **`.env.example`** and **[docs/examples/elmundi/](docs/examples/elmundi/index.md)** for one fully wired reference org.

**Docs (when published):** set `mkdocs.yml` → `site_url` to your deployed origin.  
**Package / docs version:** `0.6.0` — keep in sync with `mkdocs.yml` → `extra.doc_version` and `docs/stylesheets/extra.css` header chip.

## Documentation (MkDocs)

```bash
git clone https://github.com/<your-org>/ship.git
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

Local secrets: `.env` at this repo root (never commit). Copy from **`.env.example`**.

## Docs deployment (Docker + Bunny)

The manual is built into a static site and served by **nginx** on port **8080**, with **`GET /health`** returning `200` for Magic Containers probes.

**Local image:**

```bash
docker build -t ship-docs:local .
docker run --rm -p 8080:8080 ship-docs:local
# open http://127.0.0.1:8080/  — health: http://127.0.0.1:8080/health
```

**CI — production:** [`.github/workflows/docker-publish-bunny.yml`](.github/workflows/docker-publish-bunny.yml) (**Ship — Docker Hub + Bunny deploy**) — on every push to `main`: build, push **`latest`** and **`sha-<short>`** to Docker Hub, then [`scripts/bunny-patch-container.mjs`](scripts/bunny-patch-container.mjs) **`PATCH`**es the container template with **tag + image digest** (Bunny [rolling updates](https://docs.bunny.net/magic-containers/rolling-updates) / [patch API](https://docs.bunny.net/api-reference/magic-containers/containers/patch-container-template)), then **`POST /mc/apps/{id}/deploy`** as an extra rollout nudge.

**CI — PRs only:** [`.github/workflows/docs.yml`](.github/workflows/docs.yml) (**Ship docs (PR)**) runs `mkdocs build` on pull requests — **no Bunny**, no Docker Hub.

**GitHub → repository secrets (Actions):**

| Secret | Purpose |
|--------|---------|
| `DOCKER_HUB_TOKEN` | Push image (Docker Hub access token) |
| `BUNNY_MAIN_API_KEY` | Bunny **account** API key (Magic Containers; same header as `AccessKey` on `api.bunny.net/mc`) |

**Repository variables (optional):**

| Variable | Purpose |
|----------|---------|
| `DOCKER_IMAGE_NAME` | Image `namespace/name` (default `dekus/ship-docs` in workflow env — override for your registry) |
| `DOCKERHUB_USERNAME` | Registry username if using Docker Hub |
| `BUNNY_APP_ID` | Magic Container **application id** — set so pushes skip `POST /apps` create |
| `BUNNY_CONTAINER_NAME` | Preferred container template name (default **`Container-1`**). CI reads the real name from Bunny **`GET /apps/{id}`** and passes it to the update-image action — set this variable only if you need to disambiguate multiple templates. |
| `SHIP_MC_APP_NAME` | MC app name — default **`ship-docs`** |
| `BUNNY_REGION_IDS` | Optional — comma regions for `allowedRegionIds` |
| `SHIP_DOCS_CUSTOM_HOST` | e.g. `docs.example.com` — tailored DNS hints in workflow summary |
| `SHIP_DOCS_DNS_ZONE` | e.g. `example.com` — optional, for summary wording |

**Troubleshooting — `Create app 500`:** create the MC app once in the Bunny dashboard (or set **`BUNNY_APP_ID`**), then re-run. See workflow logs for detail.

**DNS:** after a green deploy, open the workflow **summary** — it prints Bunny host and (if **`SHIP_DOCS_CUSTOM_HOST`** is set) CNAME guidance. Otherwise configure **Custom hostnames** in Bunny and point DNS at the target they show.

You can attach a GitHub **Environment** (e.g. `ship-docs`) for approval gates; duplicate secrets there if you use environments.

## GitHub Actions (SDLC / Linear)

Workflow YAML in **your** product repository should invoke `scripts/*.mjs` from the **Ship** root (or a vendored path). Use a normal checkout and `working-directory` **`.`** when Ship is this repo; use the nested path when embedded in a monorepo. See **`examples/github-workflows/README.md`**.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). Only paths that are part of this **Ship** package are meant to be uniformly permissive; product code around them may use other terms — see `docs/legal-copyright.md`.

© 2026 Denys Kuzin
