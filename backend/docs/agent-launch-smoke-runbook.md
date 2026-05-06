# Agent launch — pre-flight smoke runbook (ELS-89)

**Audience:** Maintainer (Denys) gating the autonomous-agent pipeline before flipping it on for real customer tickets.

Run this after merging the ELS-79..88 stack and deploying. Each scenario in §2 must pass before flipping `SHIP_AGENT_PICKUP_ENABLED=true` on the prod replica set.

The unit tests bundled with each PR pin shape; this runbook covers the integration paths that need a live Linear, a live DB, and a real customer-side `shipctl run` to exercise.

---

## 1 — Setup

The smoke is run against a controlled Ship-on-Ship workspace, NOT a customer's prod workspace. Pick:

- **Workspace:** Ship-on-Ship (`d591af28-225e-477e-8448-7a4b9b06fbfc`) — has live Linear OAuth, live GitHub App install, all the agent roles seeded.
- **Repo for runs:** any activated repo where breaking a branch is harmless.
- **Tracker:** the bound elship Linear team (`854ffe38-...`).
- **Customer-side runner:** a sandbox GitHub Actions cron, NOT prod. The repo's `.ship/config.yml` should already include the decomposition routines after re-running `shipctl init` (ELS-79).

**Kill-switch starting state:** `SHIP_AGENT_PICKUP_ENABLED=false` (set in deploy env). This means **no** customer cron actually picks up work until §3 says otherwise.

---

## 2 — Smoke matrix

Each scenario walks the picker through one specific code path. Run in order; later scenarios assume earlier ones pass.

### H1 — Single safe SDLC ticket end-to-end

**Goal:** One full pass through `task_intake → ba_requirements → tech_arch_plan → dev_implementation → qa_manual → qa_automation → code_review`.

1. Create a tiny test ticket in Linear under an `active` project (state = `state:active`). Body: "Add a one-line comment to backend/app/main.py explaining the FastAPI router prefix." Title: "Smoke: comment on main.py router."
2. With `SHIP_AGENT_PICKUP_ENABLED=true` on the test deployment only, manually trigger `shipctl run --routine task_intake` from the sandbox runner.
3. Watch the audit log for `agent_run.transition` rows; each FSM stage should produce one.
4. **Pass:** ticket reaches `code_review` with a PR opened against the test repo, no `agent_run.orphan_skipped` / `priority_skipped` / `overlay_frozen_skipped` rows for it.
5. **Fail modes to look for:**
   - Ticket gets stuck mid-chain → check the agent's `outcome` payload.
   - Two PRs opened for the same ticket → ELS-85 (race) is not protecting; investigate the lock key.

### H2 — Parked-project ticket NOT picked

**Goal:** ELS-80 + ELS-82 gate.

1. Move the test project to `parked` via the Navigator's `set_priority_state` (or POST to `/v1/.../priorities/{id}/state` directly).
2. Create a fresh ticket under that project at `task_intake` stage.
3. From sandbox: `shipctl run --routine task_intake`.
4. **Pass:** the call returns `ticket: null`. Audit log has `agent_run.priority_skipped` with `priority_state="parked"` for the ticket. No Cursor run spawned.
5. Move the project back to `active` — same routine call now picks the ticket up.

### H3 — Two parallel runs don't double-pick

**Goal:** ELS-85 advisory lock.

1. Stage three tickets at `task_intake`. Two from `active` projects, one orphan.
2. Fire two `shipctl run --routine task_intake` calls within 2 seconds (a quick `&` from the sandbox shell, or two GH Actions dispatches).
3. **Pass:** one call gets a ticket; the other returns `ticket: null` (lock contention) — never the same ticket twice. Both calls audit-log; the loser shows no transition.
4. **Fail modes:**
   - Both calls get the same ticket → lock key is wrong (check the BLAKE2b derivation; re-run the unit tests).
   - Both noop with `null` → may be coincidental (the only `active` ticket happened to be the orphan and got skipped); seed a fresh `active` ticket and retry.

### H4 — Empty knowledge → `needs_clarification`, never invented

**Goal:** Agent doesn't fabricate when KB is empty.

1. Create a ticket on a topic Ship's KB has no coverage of: "Migrate the Foobar acquisition module to the new pricing engine." (Foobar doesn't exist in the codebase.)
2. Run `shipctl run --routine ba_requirements` against it.
3. **Pass:** the BA finishes with `outcome=needs_clarification`, the ticket gets a `needs:clarification` label, and the next picker call (H5) skips it.
4. **Fail mode:** BA writes a fake spec citing fake files. Investigate the role prompt and the agent's `knowledge_search` call log.

### H5 — `needs:clarification` overlay → skip + audit

**Goal:** ELS-84 overlay-label filter.

1. Take the ticket from H4 (now tagged `needs:clarification`).
2. Run `shipctl run --routine ba_requirements`.
3. **Pass:** picker returns `null`, audit log has `agent_run.overlay_frozen_skipped` with `matched_labels=["needs:clarification"]`.
4. Clear the label; repeat — now picker returns the ticket.

### H6 — End-to-end decomposition: Drafts → Parked

**Goal:** ELS-79 + ELS-81 + ELS-86 round-trip.

1. From the Navigator chat in the test workspace: ask it to `create_project` for a small initiative ("Refactor health endpoints to share a base class"). Confirm. Project lands in **Drafts**, anchor created tagged `planning:anchor`.
2. From the dashboard, click **Hand off to decomposition**. Anchor moves to `stage:wbs`.
3. Wait for sandbox cron ticks to pick up `wbs → architecture → test_architecture → tasks → planning_done`. (Or fire each routine manually for speed.)
4. **Pass:** anchor reaches `planning_done`. Project body has `## Brief / ## WBS / ## Architecture / ## Test architecture / ## Tasks` sections. Dashboard shows the project in the **Parked** bucket — NOT Active. Each child ticket created by the `tasks` stage lives in the same Linear project.
5. Pick one of the child tickets, run `shipctl run --routine task_intake`. The intake's prompt should include a `## Project context` block lifted from the parent project body (ELS-86).

### H7 — Orphan ticket → skipped + inbox notification

**Goal:** ELS-83 orphan filter.

1. Create a ticket in Linear directly (NOT via Navigator's `create_project`/`create_ticket`) — leave it without a project link.
2. Stage it at `task_intake`.
3. Run the routine.
4. **Pass:** picker returns `null` (or the next non-orphan ticket if any). Audit log: `agent_run.orphan_skipped`. Inbox: one new "Orphan tickets skipped at stage task_intake" item naming the ref.

### H8 — Kill-switch fully gates pickup

**Goal:** ELS-88 env flag.

1. Set `SHIP_AGENT_PICKUP_ENABLED=false` and restart API replicas.
2. Run any `shipctl run --routine X`.
3. **Pass:** call returns `ticket: null`. Audit log: `agent_run.pickup_disabled`. No tracker call made (Linear request count flat).
4. Flip back to `true`, restart, repeat — normal behaviour returns.

---

## 3 — Sign-off + flip

When all 8 scenarios pass:

1. Set `SHIP_AGENT_PICKUP_ENABLED=true` on the prod replica set.
2. Confirm Ship-on-Ship's `.ship/config.yml` carries the decomposition routines (regenerate via `shipctl init` if it predates ELS-79).
3. Pick **one** known-safe ticket on Ship-on-Ship to ride through the live pipeline as the canary; watch logs.
4. After the canary ticks through cleanly, agents are open for business on the workspace.

If any scenario fails, file a Linear ticket against the responsible PR and pause the launch. The kill-switch (`SHIP_AGENT_PICKUP_ENABLED=false`) buys time without a code revert.

---

## Reference: which PR each scenario covers

| H | Covers | PR |
|---|---|---|
| H1 | end-to-end SDLC | (system) |
| H2 | priority gate | #170 (ELS-80) + #172 (ELS-82) |
| H3 | pick race | #177 (ELS-85) |
| H4 | KB-empty → clarification | (existing role prompts) |
| H5 | overlay-label filter | #173 (ELS-84) |
| H6 | decomposition + Parked + project ctx | #167 (ELS-79) + #171 (ELS-81) + #175 (ELS-86) |
| H7 | orphan filter | #169 (ELS-83) |
| H8 | kill-switch | #176 (ELS-88) |

Plus #174 (ELS-72 ``repo_symbols``) lands the AST tool the agents use during code work — exercised opportunistically in H1's developer stage and any future H6 child-ticket runs.
