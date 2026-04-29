# Ship console

The console is the product workspace UI for Ship. It is a separate Next.js app from the marketing landing and talks to the FastAPI backend over `/v1`.

It is built for product owners, leads, and platform teams who need one place to see connected repos, tracker-backed work, Inbox decisions, knowledge, integrations, members, policy, audit, and repo health.

## Current wiring

- **Auth** — local/auth provider session wiring through `/api/auth/*` and `/v1/auth/*`.
- **Workspace home** — live dashboard from `/v1/workspaces/{id}/dashboard` when the API is configured; mock fixtures only when no API is configured.
- **Onboarding** — GitHub App install, repo activation, preset/wizard seed, tracker binding, agent secrets, knowledge seed, and repo intel harvest through `src/app/api/onboard/*` proxy routes.
- **Repos** — activated repo list, per-repo pages, repo settings, and repo secrets.
- **Inbox** — clarifications, improvements, failures, approvals, and routing-backed disposition surfaces.
- **Knowledge** — bucket index/detail, imports, distiller/sync surfaces, and assistant context.
- **Integrations** — workspace-level native integrations and tracker binding.
- **Catalog** — patterns, tools, collections, and workspace-private catalog layers.
- **Policy, members, audit, chat** — workspace settings and review surfaces.

When `SHIP_API_URL` is unset, the console renders mock fixtures and badges the UI as mock. With `SHIP_API_URL` set, server components and route handlers call the live `/v1` backend.

## Run

From the repo root:

```bash
npm install --prefix console
make dev-console
```

The console defaults to port 3001 so it does not collide with the landing app on port 3000. Open <http://localhost:3001>.

For backend + console together:

```bash
make dev-local
```

For Docker, the `console` service in `docker-compose.yml` wires `SHIP_API_URL=http://ship-server:8100` and exposes host port 3001:

```bash
docker compose up -d console
```

Do not run another console Next.js process while port 3001 is already served.

## Auth wiring

- Login/signup post to `/api/auth/{login,signup}`.
- The route handler calls `/v1/auth/*`, sets the httpOnly `ship_session` cookie, and redirects back into the app.
- Server components and route handlers read the cookie via `getSessionToken` and forward it as `Bearer ...`.
- Logout is POST-only so prefetching links cannot expire the session accidentally.

## Onboarding shape

The current onboarding flow is workspace and repo driven:

1. Start or resume the workspace.
2. Install or confirm the GitHub App.
3. Activate one or more repos.
4. Seed the selected preset/bundle through the wizard seed routes.
5. Bind tracker configuration for the repo/workspace.
6. Configure agent secrets through the repo secret flow.
7. Seed knowledge and harvest repo intelligence where enabled.
8. Land on the workspace home with dashboard, repo health, and Inbox visibility.

The source of truth for this flow is the route code under `src/app/api/onboard/` and the backend `/v1` routes, not old `/v1/onboarding/*` notes.

## Routes

| Path | Purpose |
| --- | --- |
| `/` | Workspace home: health, work in progress, shipped work, automation signals, repo update banners. |
| `/login` | Sign-in screen. |
| `/onboarding` | Workspace/repo setup flow. |
| `/inbox` | Decision surface for clarifications, improvements, failures, approvals, and exceptions. |
| `/knowledge` | Knowledge index and bucket management. |
| `/chat` | Workspace assistant. |
| `/integrations` | Connected providers and native integrations. |
| `/members` | Workspace members and roles. |
| `/settings` | Workspace settings and repo/catalog configuration. |
| `/settings/policy` | Workspace prose-rule policies. |
| `/audit` | Audit trail. |
| `/r/[owner]/[repo]` | Per-repo home. |
| `/repos/[id]/secrets` | Repo-managed agent/API secrets. |

## Tests and demos

The full browser coverage lives in [`../e2e/README.md`](../e2e/README.md). The product tour walks wizard, dashboard, pipelines, clarifications, improvements, feedback, navigator, knowledge, catalog, repo secrets, metrics, settings, members, integrations, audit, and back to dashboard.

## Theming

Tailwind v3 uses the same brand palette as `landing/`: `ink`, `mist`, `coral`, `aqua`, `lilac`, `sun`.
