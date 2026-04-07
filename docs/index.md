# Ship

**Version** 0.6.0 — see [Documentation versioning](#documentation-versioning).

**Ship** is the framework; this site is the manual. It is meant to read like a **short book** — opinionated, mostly **linear**, and **split on purpose** into parts you open from the top tabs.

When hosted, the canonical URL is **https://ship.elmundi.com**. Until then, build locally — see below.

---

## A letter to the reader

We wrote this because **a working loop beats a loud demo**. You can read it **cover to cover** once and actually finish, or **look things up** when you already know which part you need — both are fine.

**Linear read:** you get the pattern first (quiet-by-design agents, deterministic picks, one delivery role per window, separate audit universe, explicit trust boundaries), then how prompts live **in git**, then which **tools** are adapters, then **ElMundi** as the reference org — the public monorepo wiring with filenames, cron, domains, and secrets spelled out honestly.

**Lookup:** jump straight to **[Examples → ElMundi](examples/elmundi/index.md)** for receipts, or to *When things break* on Framework when something is on fire.

The repo history behind this is not magic — it is **fixes and fences**: idempotent PR creation when Cloud Agent runs got eager, **Todo-only** picks scoped for ElMundi’s pre-release lane, **fail closed** in CI when `LINEAR_API_KEY` is missing, team-scoped workflow states so “start” and queue snapshots match reality. Those commits are the substrate; the Framework tab is the story we tell on top.

!!! note "From the ship log"
    **Duplicate PRs** were a real failure mode — we tightened PR creation so the same ticket would not open twins. Idempotency beats another thread in Slack.

!!! note "From the ship log"
    **Todo-only picks** for the pre-release slice were not paperwork for paperwork’s sake; they were a fence so automation did not vacuum the wrong column of the board.

---

## Reading the Framework tab {#reading-the-framework-tab}

**Start here** (this page), **Part I — Framework**, **Part II — Prompts & workflows**, **Part III — Tools**, and **Part IV — Examples → ElMundi (reference deployment)** are each **one long page** — keep scrolling; the **left outline** is the section map. Use **top tabs** or the **menu** (☰) to switch.

On **[Framework](framework/index.md)** specifically, **jump links** below go straight into that page.

**Jump:** [The idea](framework/index.md#the-idea) · [The system](framework/index.md#the-system) · [Running the loop](framework/index.md#running-the-loop) · [Trust & boundaries](framework/index.md#trust-and-boundaries) · [Rolling it out](framework/index.md#rolling-it-out) · [When things break](framework/index.md#when-things-break) · [Vocabulary](framework/index.md#vocabulary) · [Buying & procurement](#buying-and-procurement) · [Documentation versioning](#documentation-versioning)

---

This site is the **Ship** manual — a governed pattern for SDLC automation — not a dump of repo files. After the framework, read **Prompts & workflows**, **Tools**, and **Examples** (today: **ElMundi** in the public monorepo).

### The four tabs (what to expect)

| Tab | What it is |
|-----|------------|
| **Framework** | The **pattern**: quiet-by-design agents, deterministic picks, one delivery role per window, separate audit universe, explicit trust boundaries. **No** requirement to use our vendors. |
| **Prompts & workflows** | **Discipline**: prompts in git, iterative tightening, catalogs of roles and workflow *patterns*. |
| **Tools** | **Adapters** we use today (Linear, GitHub Actions, Cursor Cloud Agent, Playwright, Snyk) and what each is responsible for. |
| **Examples** | **Reference wiring** — today **ElMundi** in the public monorepo (YAML filenames, Linear projects, cron, domains, secrets). More example orgs may appear here later. |

### Who this is for

- **You are adopting Ship** — read the framework once, then skim **Examples** and live in **Tools** while you wire your repo.  
- **You are evaluating for procurement** — focus on *The idea*, *The system*, *Trust & boundaries*, and [Buying & procurement](#buying-and-procurement) on **Start here**; use **Examples** for receipts.  
- **You are an operator on call** — bookmark *When things break* on the Framework page and your org’s **Examples** runbooks.

### How this maps to the repository

The **Ship** package lives in **[ElMundiUA/ship](https://github.com/ElMundiUA/ship)** at repository root: `docs/`, `scripts/`, `cloud-prompts/`, and the Node CLI.

**ElMundi** still wires it inside **[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)** under `tools/linear-agent/` (mirror until we submodule or pin **ship**). **Ship** as a **pattern** does not depend on either path. **Examples → ElMundi** does — it is “how that monorepo runs Ship today.”

**Version** 0.6.0 (header chip) — see [Documentation versioning](#documentation-versioning) on this page.

### Also on this site

- **[Iterating on prompts](prompts-workflows/index.md#iterating-on-prompts)** — the habit that makes prompts safe (Part II — Prompts & workflows).  
- **[Examples → ElMundi](examples/elmundi/index.md)** — map every abstract noun to a concrete file.

---

## Read in this order

1. Skim **[Reading the Framework tab](#reading-the-framework-tab)** above, then open **[Framework](framework/index.md)** and scroll from *The idea* through *Vocabulary* / *When things break*. **Buying & procurement** and **Documentation versioning** stay on **Start here** (below).  
2. Within Framework: [The idea](framework/index.md#the-idea) · [The system](framework/index.md#the-system) · [Running the loop](framework/index.md#running-the-loop) — or just scroll.  
3. **[Prompts & workflows → Iterating on prompts](prompts-workflows/index.md#iterating-on-prompts)** — the habit that turns bad runs into better prompts **without** losing auditability.

Then open **[Examples → ElMundi](examples/elmundi/index.md)** when you want **filenames, cron minutes, domains, and secrets** — the receipts chapter.

---

## Build this site locally

```bash
git clone https://github.com/ElMundiUA/ship.git
cd ship
python3 -m venv .venv-docs
source .venv-docs/bin/activate
pip install -r requirements-docs.txt
mkdocs serve
```

**PDF:** [PDF & offline](pdf-export.md).

---

## Buying & procurement {#buying-and-procurement}

### What this is (and is not)

This documentation describes an **architecture pattern** (**Ship**) and a **reference implementation** in a public repository. It is not automatically a commercial “documentation product” you can invoice as a line item unless your organisation packages it that way.

Copyright and redistribution follow [Legal & copyright](legal-copyright.md) unless you sign something else.

---

### What to verify commercially

Treat each layer as its **own** vendor relationship:

| Layer | Typical questions |
|-------|-------------------|
| **CI** | Minute granularity, secret storage, audit logs, egress limits. |
| **Tracker** | API stability, SSO, field-level permissions, retention. |
| **Agent provider** | Data handling, subprocessors, concurrency, export of usage. |
| **Scanners** | False positive rate, noise into tracker, license for CI use. |

**IP:** prompts and skills in-repo are usually **employer-owned**. Clarify before you redistribute them externally or train on them outside policy.

**Exit:** orchestration is plain CI + scripts; swapping tracker or agent is **adapter work**, not a rewrite — see [The idea](framework/index.md#the-idea) and [The system](framework/index.md#the-system).

---

### No implied SLA

Unless **your** organisation sells support, these pages carry **no** service level promise. Operators use [When things break](framework/index.md#when-things-break) and the **Examples** runbooks.

If a stakeholder needs an SLA, that is a **contract** discussion — not a documentation footnote.

---

### Metrics that survive the first budget review

Prefer **operational** measures over magic ROI slides:

- **Todo → first PR** time for tickets that meet guards (median and p95).  
- **Repeatability** — same guard + same ticket shape → same automation path.  
- **Auditability** — % of automated touches with ticket comment + workflow link.  
- **Bounded WIP** — depth of Todo vs merges per week (are you feeding review a firehose?).

“Hours saved by AI” without definitions is astrology. The numbers above are **boring** — which is why finance and security teams trust them.

---

## Documentation versioning {#documentation-versioning}

**Purpose:** explain what the **docs version** means and where it is stored.  
**Audience:** anyone publishing or reviewing this manual.

### Current version

- **0.6.0** — shown in the site header chip (`docs v0.6.0`), referenced from this page (**Ship**) and [Legal & copyright](legal-copyright.md). Canonical site URL: `mkdocs.yml` → `site_url` (https://ship.elmundi.com/).
- Source of truth: `mkdocs.yml` → `extra.doc_version` (keep **in sync** with the chip in `docs/stylesheets/extra.css`).

### Policy (practical)

- **Major (X.0.0)** — navigation restructure, new stakeholder pack, or breaking rename of workflows/pages that readers bookmark.
- **Minor (x.Y.0)** — new runbooks, materially new sections, or significant accuracy fixes across multiple pages.
- **Patch (x.y.Z)** — typos, clarifications, diagram refresh, single-page fixes.

Documentation version is **independent** of application semver unless you explicitly choose to align them in release notes.

### Related

- [PDF & offline](pdf-export.md) for exporting a versioned PDF.

---

## License {#license}

This repository (**[ElMundiUA/ship](https://github.com/ElMundiUA/ship)**) is the **Ship** framework slice: manual, Node CLI, `cloud-prompts/`, and `scripts/`, offered under the **Apache License, Version 2.0** unless a file says otherwise. Full text: [`LICENSE`](https://github.com/ElMundiUA/ship/blob/main/LICENSE) at this repo’s root.

The **ElMundi** product monorepo (**[ElMundiUA/elmundi](https://github.com/ElMundiUA/elmundi)**) is **mixed**: only the path that tracks this extraction (`tools/linear-agent/`) was ever meant to read as permissively licensed; **website**, media, and product code may stay **proprietary**. Do not assume the monorepo root `LICENSE` covers the whole tree there—see [Legal & copyright](legal-copyright.md).

---

## Legal

[Legal & copyright](legal-copyright.md).
