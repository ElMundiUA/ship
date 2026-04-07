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

**CI:** [`.github/workflows/docker-publish-bunny.yml`](.github/workflows/docker-publish-bunny.yml) — on every push to `main`: build, push to Docker Hub, then call Bunny’s **container-update-image** action.

**GitHub → repository secrets (Actions):**

| Secret | Purpose |
|--------|---------|
| `DOCKER_HUB_TOKEN` | Push image (Docker Hub access token) |

**Repository variables (optional):**

| Variable | Purpose |
|----------|---------|
| `DOCKER_IMAGE_NAME` | Default `dekus/ship-docs` — override if you use another Hub repo |
| `DOCKERHUB_USERNAME` | Default `dekus` |

**GitHub Environment `ship-docs`** (recommended — holds Bunny + prod-like vars):

| Name | Purpose |
|------|---------|
| `vars.BUNNY_APP_ID` | Magic Containers **application** ID for Ship (separate app from elmundi frontend) |
| `vars.BUNNY_CONTAINER_NAME` | Container name inside that app (default in workflow: `ship`) |
| `secrets.BUNNY_MAIN_API_KEY` | Bunny API key (exchange flow; same family as elmundi — not storage-only keys) |

**Bunny dashboard (new app):** create a **Magic Container** application, add one container named **`ship`** (or match `BUNNY_CONTAINER_NAME`), image `dekus/ship-docs:latest` (or your `DOCKER_IMAGE_NAME`), **container port 8080**, HTTP health path **`/health`**. After the first manual deploy, CI can update the tag on each `main` push.

## GitHub Actions (SDLC / Linear)

Workflow YAML in ElMundi’s monorepo lives under `.github/workflows/` and historically assumed checkout of `tools/linear-agent/`. When adopting **ship** as its own repo, set `working-directory` to **`.`** and use a normal full checkout. See **`examples/github-workflows/README.md`** for migration notes and links to the canonical files in **elmundi**.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). The ElMundi **product** monorepo remains mixed; only this package is meant to be uniformly permissive. See `docs/legal-copyright.md`.

© 2026 Denys Kuzin
