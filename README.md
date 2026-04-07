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

## GitHub Actions (SDLC / Linear)

Workflow YAML in ElMundi’s monorepo lives under `.github/workflows/` and historically assumed checkout of `tools/linear-agent/`. When adopting **ship** as its own repo, set `working-directory` to **`.`** and use a normal full checkout. See **`examples/github-workflows/README.md`** for migration notes and links to the canonical files in **elmundi**.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE). The ElMundi **product** monorepo remains mixed; only this package is meant to be uniformly permissive. See `docs/legal-copyright.md`.

© 2026 Denys Kuzin
