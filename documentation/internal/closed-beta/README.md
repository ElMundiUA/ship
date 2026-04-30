# Closed beta — epic detail files

One file per epic. The parent plan, exit criteria, and decision log live in [`../closed-beta-plan.md`](../closed-beta-plan.md). Read that first.

## Epic format

Every detail file follows the same shape:

- **Goal** — one-sentence outcome.
- **Why** — product reason, often pointing at a blog post or RFC.
- **Tasks** — discrete units, T01..Tnn, each ~½ to 2 days. Each task carries acceptance, files/areas touched, and a rough estimate (`S` = ½–1d, `M` = 1–3d, `L` = 3+d).
- **Definition of done** — the boolean check that closes the epic.
- **Risks / unknowns** — what could derail this; what the maintainer should triage early.
- **Out of scope** — explicit bounds; if work creeps here, it spawns a new epic.

## Index

| ID | Title | Priority | Effort |
|---|---|---|---|
| [E01](./E01-knowledge-live.md) | Knowledge bucket UI live, no mock | P0 | M |
| [E02](./E02-console-mock-cleanup.md) | Console mock fallback removal | P0 | M |
| [E03](./E03-golden-path-audit.md) | Golden path: signup → first run, end-to-end | P0 | L |
| [E04](./E04-auth0-production.md) | Auth0 production hardening | P0 | S |
| [E05](./E05-adoption-gauntlet.md) | Three-project adoption gauntlet (ElMundi, Ship-on-Ship, .NET→Go) | P1 | XL |
| [E06](./E06-inbox-loop.md) | Inbox loop end-to-end with evidence | P1 | M |
| [E07](./E07-tracker-bindings.md) | Tracker bindings: Linear + GH Issues only | P1 | S |
| [E08](./E08-invite-only.md) | Invite-only gating + waitlist | P2 | S |
| [E09](./E09-sendgrid.md) | SendGrid email pipeline + 4 templates | P2 | M |
| [E10](./E10-observability.md) | Observability + KPI dashboard + alerts | P2 | S |
| [E11](./E11-docs-alignment.md) | Documentation alignment to code | P3 | L |
| [E12](./E12-landing-ux-finalization.md) | Landing finalize + console UX polish + mobile + demo | P3 | M |
| [E13](./E13-rip-chroma.md) | Rip Chroma, unify on pgvector | P2 | M |

## Convention notes

- File paths in tasks use repo-relative form (`console/src/app/knowledge/page.tsx`).
- "Live" means against `https://app.ship.elmundi.com` after deploy, not local dev.
- "Beta-blocking" means the closed beta cannot end until this task closes. Mark with `**[beta-blocking]**` in the task title.
- "Hotfix-eligible" means if found broken via E05 dogfood, fix immediately rather than tracking through here.
- Tasks reference RFCs by number (`RFC-0010`) or the relevant blog post slug.
