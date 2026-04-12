# Ship

<section class="ship-hero" markdown="1">
  <p class="ship-kicker">Instruction-first SDLC framework</p>
  <h1>Ship gives your agent a method, not a vendor lock-in</h1>
  <p>
    Use one guided prompt to adapt delivery, QA, release gates, and retros to your existing stack
    (Linear/Jira/GitHub Issues/spreadsheets, any CI, any agent runtime).
  </p>
  <div class="ship-hero-actions">
    <a class="md-button md-button--primary" href="getting-started/">Start in 10 minutes</a>
    <a class="md-button" href="/book">Read the book</a>
  </div>
</section>

## Choose Your Path

<div class="ship-card-grid" markdown="1">
  <a class="ship-card" href="getting-started/">
    <h3>Getting started</h3>
    <p>Prompt builder + copy-ready instruction. Fastest way to launch adoption with any agent.</p>
  </a>
  <a class="ship-card" href="adoption/">
    <h3>Adoption playbooks</h3>
    <p>Interactive setup contract, launch matrix, migration notes, and practical rollout artifacts.</p>
  </a>
  <a class="ship-card" href="tools/">
    <h3>Interfaces & API</h3>
    <p>Tracker adaptation contract and backend endpoints: <code>/search</code>, <code>/fetch</code>, <code>/feedback</code>.</p>
  </a>
  <a class="ship-card" href="examples/elmundi/">
    <h3>Reference implementations</h3>
    <p>Battle-tested wiring (ElMundi) + contribution path for new real-world setups.</p>
  </a>
</div>

## What You Get

- **Interactive onboarding** — agent asks discovery questions before changing files.
- **Portable contracts** — queue, QA split, release gates, digest/retro rhythm.
- **Evidence trail** — decisions and automation outputs stay auditable.
- **Continuous improvement** — retro feedback can become Ship backlog issues safely.

## Local run

**Next.js (manual + landing)** — from repo root:

```bash
npm install
npm run landing:dev
```

Open [http://127.0.0.1:3000/docs](http://127.0.0.1:3000/docs) for this manual.

**Backend API:**

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-backend.txt
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8100
```

## Buying & procurement {#buying-and-procurement}

Ship is methodology + prompts + reference implementations.

- No mandatory vendor bundle.
- No implicit SLA unless your org adds one.
- Exit path is explicit because interfaces are documented.

## Documentation versioning {#documentation-versioning}

Current manual version: **0.7.0** (ship with the repository).

## License

Apache 2.0 for this repository unless a file says otherwise. See [Legal & copyright](legal-copyright.md).
