# PDF & offline viewing

Think of this site as **one manual in a single volume**: everything lives under the same build, and the **parts follow the top navigation** in reading order—**Start here**, then **Getting started**, **The book**, **Prompts & workflows**, **Tools**, and **Examples → Reference org**. **Getting started** is a short procedural page; **The book** is **one long page** (scroll on screen); other tabs may also be long. There is no separate paginated “book” PDF from MkDocs itself.

The site documents **Ship**. When deployed, its URL is whatever you set in **`mkdocs.yml` → `site_url`**. It is built with [MkDocs](https://www.mkdocs.org) and [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/). The build writes static HTML to `site/`.

## Static site (offline)

From the **Ship** repository root:

```bash
python3 -m venv .venv-docs
source .venv-docs/bin/activate   # Windows: .venv-docs\Scripts\activate
pip install -r requirements-docs.txt
mkdocs build
```

Open `site/index.html`. A local server is optional: `cd site && python -m http.server`.

## Printing or saving a readable PDF

After `mkdocs serve` or `mkdocs build`, use the browser’s **Print → Save as PDF** (Chrome, Edge, or Safari). To mirror the **volume order**, export **one PDF per top-level section** in nav sequence (Start here → Getting started → The book → Prompts & workflows → Tools → Examples → Adoption), then combine if you want a single file—your OS or PDF tool can merge them.

### Readability tips

- Use the **light** color scheme before printing; contrast and code blocks usually read better on paper.
- Turn on **background graphics** if diagrams or shaded callouts look clipped or hollow.
- Prefer **margins** that match your printer; if the table of contents or wide tables truncate, try landscape for those pages or reduce scale slightly in the print dialog.

## Dependencies (quick reference)

If you already use a virtualenv:

```bash
source .venv-docs/bin/activate
pip install -r requirements-docs.txt
```

## D2 diagrams

Sources: `documentation/diagrams/*.d2`. SVG outputs are regenerated on build when the **`d2`** CLI is on `PATH`; otherwise commit SVGs (already in repo after first render).

---

© See [Legal & copyright](legal-copyright.md).
