# Closed beta plan

**Status:** active
**Owner:** Denys Kuzin
**Created:** 2026-04-30
**Target completion:** ~6 weeks (3 dogfood projects passing)

This is the working plan to take Ship from its current "console partially wired" state to a closed beta where three dogfood projects are running end-to-end on Ship. After all three pass, closed beta is over and we open signups.

Detailed task breakdowns live in [`closed-beta/`](./closed-beta/). This document is the index, the contract, and the decision log.

## Exit criteria (closed beta → public beta)

The closed beta is **done** when all three projects are running on Ship without daily intervention from the maintainer:

1. **ElMundi adoption** — `../elmundi/` (Next.js, Linear tracker, Cursor agent, GitHub Actions CI). PO is the maintainer himself. Used to validate the adoption flow against a real production project.
2. **Ship-on-Ship** — this repo runs on Ship's own console. Validates dogfooding and that the platform's own delivery works under its own rules.
3. **.NET → Go migration project** (third-party, TBD path). Validates Ship across a different language family and a migration use case, not just steady-state delivery.

If the three projects can move work, surface decisions in the Inbox, and leave evidence without hand-holding, beta is over.

## Decisions locked in this round

| Question | Answer |
|---|---|
| Billing for beta | **Invite-only**, no Stripe. `plan` field stays `free`. Signup gated by an invite token. |
| Email provider | **SendGrid.** Existing API key already in use elsewhere by the maintainer. |
| Mobile policy | **Fix.** Console must work on mobile for at least Inbox triage and dashboard read. Not a "desktop-only beta". |
| Demo video | **Record new.** The existing 25-second `demo-full-journey.wired.spec.ts` recording is unusable. New cut goes on landing hero. |
| ElMundi PO | Maintainer himself. No external dependency. |
| Documentation source of truth | **Code wins.** Where the docs and the code disagree, the code is correct and the docs get rewritten. |

## Working principles

- **No mock data in production.** When the API fails, show a real error with retry — never `MockBanner`.
- **One canonical onboarding path.** Five steps, in order, no branching presets. Wizard v2 stays.
- **Knowledge is the keystone feature.** It is the proof of why a PO would buy Ship. Ship it live before anything cosmetic.
- **Dogfood drives the bug list.** Every blocker found while migrating ElMundi / Ship / the Go project becomes a P0 issue, not a future epic.
- **Trackers in beta:** Linear and GitHub Issues only. Jira / Notion / Slack channels stay hidden behind a feature flag until post-beta.

## Epic map — 13 epics, 4 priority bands

### P0 — Without these the first user fails before producing value

| Epic | Outcome | Detail |
|---|---|---|
| **E01** | Knowledge bucket UI talks to the live backend, no mock | [E01](./closed-beta/E01-knowledge-live.md) |
| **E02** | Console has zero `MockBanner` paths in production | [E02](./closed-beta/E02-console-mock-cleanup.md) |
| **E03** | Documented golden path from signup → first scheduled run, every step verified | [E03](./closed-beta/E03-golden-path-audit.md) |
| **E04** | Auth0 production flow hardened: claims, JIT-provisioning, JWKS, error UX | [E04](./closed-beta/E04-auth0-production.md) |

### P1 — Dogfood, the loop, the boundaries

| Epic | Outcome | Detail |
|---|---|---|
| **E05** | All three adoption projects (ElMundi, Ship-on-Ship, .NET→Go) running, with one blog post per project | [E05](./closed-beta/E05-adoption-gauntlet.md) |
| **E06** | Inbox loop proven for clarification / improvement / failure with evidence | [E06](./closed-beta/E06-inbox-loop.md) |
| **E07** | Linear + GH Issues tracker bindings working both ways; partials hidden | [E07](./closed-beta/E07-tracker-bindings.md) |
| **E14** | Server-side smart orchestration (CLI thin proxy; tracker FSM + adapters live in backend) — exit blocker, not post-beta cleanup | [E14](./closed-beta/E14-smart-orchestration.md) |

### P2 — Operational floor for letting strangers in

| Epic | Outcome | Detail |
|---|---|---|
| **E08** | Invite-only gating with waitlist, manual approval, lifecycle | [E08](./closed-beta/E08-invite-only.md) |
| **E09** | SendGrid wired with 4 production templates (invite / inbox new / run failure / daily digest) | [E09](./closed-beta/E09-sendgrid.md) |
| **E10** | Sentry context, uptime monitor, KPI dashboard, alert rules | [E10](./closed-beta/E10-observability.md) |
| **E13** | Rip out ChromaDB; unify all vector search on pgvector | [E13](./closed-beta/E13-rip-chroma.md) |

### P3 — Trust, presentation, finalization

| Epic | Outcome | Detail |
|---|---|---|
| **E11** | All `documentation/*.md` and `landing/content/*` aligned with current code | [E11](./closed-beta/E11-docs-alignment.md) |
| **E12** | Landing links to app, mobile fixed, demo recorded, empty states everywhere | [E12](./closed-beta/E12-landing-ux-finalization.md) |

## Suggested execution order

This is a recommended cadence; reality will reshuffle.

| Week | Focus |
|---|---|
| 1 | E03 (golden path audit, **legacy-model S3 walk only — baseline**) **+** E01 (knowledge live) **+** E02 (mock cleanup). Fix bugs surfaced in S3 hot. |
| 2 | **E14** (server-side smart orchestration) — first major in-beta architecture move; the rest of the walks happen on the new model. **+** E04. |
| 3 | E05 Ship-on-Ship + ElMundi adoption on the smart-server model. **+** E06 (Inbox loop), **+** E07 (tracker cleanup). |
| 4 | E08 invite-only **+** E09 SendGrid **+** E10 observability **+** E13 (rip Chroma). Open soft beta to 3-5 hand-picked friends. |
| 5 | E05 .NET→Go (track 3) — completes exit criteria. **+** E12 (landing finalization, demo, mobile). |
| 6 | E11 (docs alignment) — last because docs lag code by design. Open public signups. |

## What is **not** in this plan (out of scope for closed beta)

- Stripe / billing infrastructure beyond the `plan` enum
- SSO providers other than Auth0 (Okta, Azure AD, etc.)
- Multi-region / data residency beyond a single Bunny region
- Self-hosted / on-prem distribution
- Native mobile app
- Slack / Teams native integrations
- Marketing site beyond `ship.elmundi.com` (no separate landing per language)
- Status page (use Better Uptime page link only)

These come back into scope when public-beta cohort metrics justify them.

## How to update this plan

- **Weekly review** — every Monday, walk the epics, mark progress, move bugs into the right epic file.
- **Source-of-truth pivot** — if a decision in the table above changes, update *here first*, then propagate to landing/docs.
- **Closing an epic** — move its detail file to `closed-beta/closed/` and add a `[closed]` note in the table above with the date.
- **Adding new work** — never inline new tasks in the parent. Add to the matching epic's detail file. New epics need a one-line justification at the top of this file.

## Related references

- Product blog (the why): `landing/content/blog/`
- The book (the philosophy): `landing/content/book.md`
- RFCs (the protocol): `documentation/protocol/`
- Pilot plan (older, narrower): [`pilot-plan.md`](./pilot-plan.md)
- Console refactor backlog (technical): [`console-refactor-backlog.md`](./console-refactor-backlog.md)
