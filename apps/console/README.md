# Ship console

The web console for Ship — deliberately small. Since the MCP-first
rework (thesis 9, ELS-279), Ship is **operated from the agent the
operator already lives in** (Claude Code / Claude Desktop attached over
MCP, Telegram for mobile), and the work itself lives in the tracker
(Linear) and GitHub. The console is not a destination; it is the trust
bootstrap plus the few surfaces that genuinely cannot leave the
browser. It is a separate Next.js app from the marketing landing and
talks to the FastAPI backend over `/v1`.

## What the console is for

| Surface | Route | Why it's web |
| --- | --- | --- |
| Operator hub | `/` | "Engine alive? Anything waiting on me? How do I connect my agent?" in one screen |
| Connect-your-agent | card on `/` and the onboarding finale | PAT mint (one-time secret reveal) + copy-ready `claude mcp add` command |
| Approve confirm | `/approve/{id}` | Deep-link target for Telegram approval buttons and MCP `web_url` refusals; typed-slug confirm for destructive items — by stakes policy the ONLY place those can be approved |
| Navigator chat | `/chat` | Fallback thin client when the operator has no agent at hand |
| Settings | `/settings/*` | Config-scope renderer, integrations/OAuth wiring, Agents & access (PATs), danger zone |
| Onboarding | `/onboarding` | GitHub App install, repo activation, wizard seed, tracker binding — ends at "Attach Ship to your agent" |

There is **no Inbox page**. Inbox triage happens through the
operator's agent (`inbox_list` / `inbox_get` / `inbox_update` over
MCP) and Telegram. The `inbox_items` table and REST API are unchanged
— only the mailbox viewer was removed (ELS-289/294). The hub shows a
"Waiting on you" strip (actionable count + top 3) linking each item to
its `/approve/{id}` page.

Process (`/process`), Knowledge (`/knowledge`) and Policies
(`/settings/policy`) remain routable but left the navigation rail —
they're linked from Settings → General → Advanced surfaces. The
console never grows back dashboards/analytics: domain views live in
the tracker and GitHub; run introspection belongs to MCP tools and
engine-health.

## Attaching an agent

Mint a PAT (hub card or Settings → Agents & access), then:

```bash
claude mcp add ship https://api.ship.elmundi.com/mcp -t http \
  -H "Authorization: Bearer <pat>"
```

Claude Desktop: Settings → Connectors → Add custom connector with the
same URL + header. The MCP endpoint shown in the UI comes from
`NEXT_PUBLIC_SHIP_MCP_URL` (defaults to the prod endpoint).

## `console.surface` — how much console to show

Per-workspace config scope (Settings → Config) beats the
`SHIP_CONSOLE_MODE` env default beats `full`. Resolution lives in
`src/lib/console-mode.ts`; disallowed paths 302 to the hub (see
`src/app/(authed)/layout.tsx`).

| Mode | Reachable paths | Rail |
| --- | --- | --- |
| `full` (default) | everything | Chat · Settings |
| `residual` | `/`, `/approve/*`, `/chat`, `/settings*` | Chat · Settings |
| `off` | `/`, `/approve/*` | none |

**Recommended posture: `residual`.** It keeps the hub, the approve
surface, the fallback chat and settings, and turns everything else
dark — matching how an MCP-attached operator actually works. `full`
stays as the opt-in escape hatch for teams that want the advanced
surfaces in the browser. `/approve/*` is reachable in EVERY mode —
pending approvals must never be orphaned (thesis 4).

## Run

From the repo root:

```bash
npm install --prefix console
make dev-console
```

The console defaults to port 3001 so it does not collide with the
landing app on port 3000. Open <http://localhost:3001>.

For backend + console together: `make dev-local`. For Docker, the
`console` service in `docker-compose.yml` wires
`SHIP_API_URL=http://ship-server:8100` and exposes host port 3001.
Do not run another console Next.js process while port 3001 is already
served.

When `SHIP_API_URL` is unset, the console renders mock fixtures and
badges the UI as mock. With `SHIP_API_URL` set, server components and
route handlers call the live `/v1` backend.

## Auth wiring

- Login/signup post to `/api/auth/{login,signup}`.
- The route handler calls `/v1/auth/*`, sets the httpOnly
  `ship_session` cookie, and redirects back into the app.
- Server components and route handlers read the cookie via
  `getSessionToken` and forward it as `Bearer ...`.
- Logout is POST-only so prefetching links cannot expire the session
  accidentally.

## Tests

Unit: `npx vitest run` (console-mode allowlists, approve deep-link
helper, tool renderers). Wired browser coverage lives in
[`../../e2e`](../../e2e) — `console-flows.wired.spec.ts` pins the
hub/rail/approve IA.

## Theming

Tailwind uses the same brand palette as `landing/`: `ink`, `mist`,
`coral`, `aqua`, `lilac`, `sun`.
