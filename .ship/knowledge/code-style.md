# Code style

Starter code-style bucket seeded by Ship on first repo activation.
Replace any section that does not match your team — this file is
committed to your repo as `.ship/knowledge/code-style.md` and Ship
reads it verbatim when onboarding every new agent / contributor.

## Languages & tooling

- **TypeScript** — `strict: true`, no implicit `any`. Run `tsc --noEmit`
  in CI; never suppress errors with `@ts-ignore` (use `@ts-expect-error`
  with a link to the ticket that will remove it).
- **Python** — target version pinned in `pyproject.toml`. Format with
  `ruff format`, lint with `ruff check`. Type check with `mypy` or
  `pyright` in the `backend/` package.
- **JS/TS formatting** — Prettier (`.prettierrc` at repo root). Two-
  space indent, double quotes, trailing commas on ES5-legal positions.
- **Commit hooks** — lint + typecheck must run locally via
  `husky` / `pre-commit`. The CI gate is a safety net, not the primary
  feedback loop.

## Naming

- Filenames: `kebab-case.ts` for modules, `PascalCase.tsx` for React
  components, `snake_case.py` for Python modules.
- Functions: `camelCase` in JS/TS, `snake_case` in Python.
- React components: `PascalCase`. Hooks always start with `use…`.
- Environment variables: `SCREAMING_SNAKE_CASE`; secrets go through a
  secrets manager, never `.env` in a PR.

## Error handling

- Never swallow an exception silently — either re-throw with context
  or log at `error` level with structured fields.
- User-facing errors: map to a typed `UserError` / `AppError` that the
  UI or API layer translates into a status code + message. No leaking
  stack traces to end users.
- Sentry: wrap non-trivial operations in `Sentry.startSpan({ op, name })`
  and capture exceptions with `Sentry.captureException(err)` inside
  `catch` blocks.

## Testing

- Every new feature ships with at least one test at the layer where the
  behaviour lives (unit for pure logic, integration for route/DB, E2E
  only for cross-layer flows).
- Test file names mirror the module: `foo.ts` → `foo.test.ts`.
- No `skip` / `todo` without a ticket link in the annotation.

## Imports & modules

- Absolute imports from the package root (`@/…` alias, or `backend.app.…`).
- One export per file when the export *is* the file (e.g. a component).
- Side-effect imports only at the package entry point (`main.py`,
  `instrumentation-client.ts`) — never mid-tree.

## Comments

- Comments explain **why**, not **what**. If the code needs a comment
  to describe what it does, rename variables / extract a function first.
- No "changelog comments" (`// 2024-01-01 fixed bug`) — Git history does
  this job better.

## Review checklist

- Does the diff do one thing? If it touches lint + a feature + a refactor,
  split it.
- Any new public API documented? (docstring, OpenAPI, Storybook.)
- Any new env var documented in `.env.example` + the relevant README?
- Any migration (DB schema, client contract) called out in the PR body?

## Replace this file

This is Ship's starter. Delete the sections that do not apply and add
team-specific conventions (e.g. preferred UI library patterns, DB
access conventions, specific lint rules you enforce). The file lives
in your repo — edit it normally, commit, push. Ship picks up the
updated content on its next knowledge refresh.
