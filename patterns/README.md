# Org patterns manifest

`manifest.json` lists **reviewable instruction slices** (prompts) the Ship landing exposes under **Patterns**. From the Ship repo, list and fetch bodies with the CLI: `npm run ship -- pattern list` and `npm run ship -- pattern show <id>` / `npm run ship -- pattern fetch <id>` (same data as `GET /patterns`, `GET /patterns/{id}`, or `POST /fetch` with `{ kind: "pattern", id }` on the methodology API).

Edit the manifest in pull request with normal code review; paths must stay under the repository root and point to `.md` files only.
