# Org patterns manifest

`manifest.json` lists **reviewable instruction slices** (prompts) the Ship landing exposes under **Patterns**. From the Ship repo, list and fetch bodies with the CLI: `npm run ship -- patterns list` and `npm run ship -- patterns show <id>` (same data as `GET /patterns` and `GET /patterns/{id}` on the methodology API).

Edit the manifest in pull request with normal code review; paths must stay under the repository root and point to `.md` files only.
