# Repo git hooks

Hooks checked into the repo so every contributor runs the same
pre-commit guards the CI runs on every PR — caught locally before
you push, not after you've opened (and broken) a PR.

## One-time setup

From the repo root:

```bash
npm run hooks:install
```

That sets `git config core.hooksPath .githooks` for your clone.
You only need to run it once per clone.

## What runs on `git commit`

1. **Release-version sync** — `node scripts/version.mjs check`.
   Equivalent to the `version-check` workflow. Fails if any of
   `VERSION`, `package.json`, `landing/package.json`,
   `console/package.json`, `cli/package.json`, `e2e/package.json`,
   or `backend/app/main.py` drift.
2. **Bundle-version drift** — `node scripts/bundle-version-check.mjs`.
   Only triggered when the staged diff touches any seed-bundle
   source (starter workflows, `catalog.py`, `seed_bundle.py`,
   etc.). Fails unless `BUNDLE_VERSION` was bumped in the same
   commit. Equivalent to the `bundle-version-check` workflow.

## Bypass

For rare cases (mid-rebase, fix-up commit, etc.):

```bash
git commit --no-verify
```

The CI still enforces both checks on the PR, so you can't sneak
past them, only defer the pain.
