# @elmundi/ship-cli

Command-line entry to the Ship methodology: **one HTTP API** (FastAPI) for **search, fetch, feedback, patterns, tools, workflows, collections** — or read catalogs from disk inside a Ship clone / `SHIP_REPO` — plus **`ship init`** to inject API usage into agent configs.

Published under the npm org **[elmundi](https://www.npmjs.com/org/elmundi)**; the binary name remains **`ship`**.

## Requirements

- **Node.js 20+** (matches Ship CI and typical adopters).

## Install

After the package is [published to npm](https://www.npmjs.com/package/@elmundi/ship-cli):

```bash
npm install -g @elmundi/ship-cli
# or, without a global install:
npx @elmundi/ship-cli help
```

From a full **Ship** monorepo clone you can still run `npm run ship -- …` from the repo root (workspace).

## Adopt without cloning the whole monorepo

1. Install the CLI (`npm i -g @elmundi/ship-cli` or use `npx @elmundi/ship-cli`).
2. From **any** directory, point **`SHIP_API_BASE`** at the **deployed methodology API** and list patterns or catalogs (same server as search):

   ```bash
   SHIP_API_BASE=https://your-ship-api.example.com npx @elmundi/ship-cli pattern list
   SHIP_API_BASE=https://your-ship-api.example.com npx @elmundi/ship-cli tool list
   ```

3. Optional: work from a **local** Ship checkout (or **`SHIP_REPO`**) to read manifests from disk without calling the API.

4. In your **product** repository, wire agents to the methodology API:

   ```bash
   cd /path/to/your-product
   npx @elmundi/ship-cli init --yes
   ```

   Use **`--dry-run`** first to preview; **`--yes`** skips prompts and writes files — see `ship init help`.

## Which commands need what

| Command | Needs |
|--------|--------|
| `ship pattern|tool|workflow|collection …` (plural aliases) | Same **`SHIP_API_BASE`** as search/docs when not on disk. **Local:** cwd inside Ship or **`SHIP_REPO`**. |
| `ship search`, `ship docs fetch|feedback` | **`SHIP_API_BASE`** (default public methodology URL; override locally) or `--base-url`. |
| `ship init` | Target repo cwd; **`SHIP_API_BASE` / `--base-url`** is the API URL written into snippets. |

## Publishing (maintainers)

Releases are published via GitHub Actions (**Publish @elmundi/ship-cli to npm**): `npm publish -w @elmundi/ship-cli` from the monorepo root (not `npm publish --prefix cli`, which would try to publish the private root package). Configure the **`NPM_TOKEN`** repository secret. On npmjs.com use either a **Granular Access Token** with **Publish** on **`@elmundi/ship-cli`** (or the **elmundi** org) and **“Bypass two-factor authentication”** enabled for automation, or a classic **Automation** token — classic **Publish** tokens often cannot publish from CI when 2FA is on (`E403` *Two-factor authentication or granular access token with bypass 2fa…*).

The root monorepo `package.json` stays **`private`: true**; only **`@elmundi/ship-cli`** is intended for the public registry.

## Semver

Package version lives in **`cli/package.json`**. Bump it for each npm release following [semver](https://semver.org/).
