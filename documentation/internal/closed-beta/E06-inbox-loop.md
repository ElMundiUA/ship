# E06 — Inbox loop end-to-end with evidence

**Priority:** P1
**Effort:** M (~5–7 days)
**Owner:** TBD

## Goal

For each of the five Inbox shapes — clarification, improvement, approval, failure, exception — there is a verified path from "agent emits the signal" to "human resolves it" to "evidence is on the record". This is the primary product loop and must be airtight.

## Why

The blog post **"The Inbox is not a backlog"** sets the standard: items are decisions waiting for an owner, not work waiting in a queue. The product cannot honestly claim that loop until each shape has been proven on real data with real evidence.

Backend already supports it: `routes/inbox.py`, `routes/inbox_routing.py`, `routes/inbox_groups.py`, `routes/clarifications.py`, `routes/improvements.py`, plus `services/inbox/` (routing, dual_write, side_effects). The DB already has `inbox_items` + `inbox_item_events`.

## Tasks

### T01 — Document the contract for each shape **[S]**

- Living doc: `documentation/internal/inbox-shapes.md`.
- For each of the 5 shapes:
  - What ingress route accepts it (pipeline endpoint or sync endpoint).
  - What `inbox_items.kind` value is set.
  - What `side_effects` fire on disposition (accept / decline / defer / resolve / reassign / snooze).
  - What evidence ends up where (tracker comment, PR check, knowledge article, audit log).

**Acceptance:** the doc covers all 5 shapes; matches what the code actually does today.

### T02 — Clarification: full loop **[M]**

- **Ingress:** agent calls `shipctl callback --outcome=clarification --question="..."` → POST `/v1/clarifications/pipeline`.
- **Surface:** Inbox shows the item, item detail page renders the question + the ticket context.
- **Action:** PO types the answer, picks "resolve".
- **Side effect:** answer is posted as a comment on the linked tracker item (Linear or GH Issues).
- **Evidence trail:** audit_log entry, comment URL stored on the item, item visible in a "resolved last 7d" view.

**Acceptance:** screen recording of the full loop on the ElMundi dogfood project.

### T03 — Improvement: full loop **[M]**

- **Ingress:** `shipctl callback --outcome=improvement` (e.g. "I noticed routine X could be replaced by routine Y").
- **Surface:** Inbox shows the proposal with diff (current vs proposed).
- **Action:** "Accept" applies the change (e.g. updates `.ship/config.yml` via PR), "Decline" closes with reason.
- **Side effect:** opened PR or recorded "decline reason" in audit.
- **Evidence:** PR URL, audit_log, item disposition.

**Acceptance:** at least one improvement accepted and applied via PR; one declined with reason.

### T04 — Approval: full loop **[M]**

- **Ingress:** policy that requires approval triggered by an agent action.
- **Surface:** Inbox item with policy reference, the action being requested, and the diff or call.
- **Action:** "Approve" lets the agent continue (the original run resumes via callback); "Decline" stops the run and records reason.
- **Side effect:** dispatched workflow either resumes (run_token still valid) or completes with `requires_approval=false`.

**Acceptance:** an agent run that hits a policy gate sees the gate, an Inbox item appears, approval resumes the run.

### T05 — Failure: full loop **[M]**

- **Ingress:** agent reports `--outcome=failure` or the workflow itself fails and a webhook lands.
- **Surface:** Inbox shows failure with logs link, repro hint, severity.
- **Action:** "Retry" dispatches again; "Resolve" closes; "Reassign" routes to a different owner.
- **Side effect:** retry creates a new `pipeline_runs` row linked to the original; resolve writes audit; reassign updates routing.

**Acceptance:** intentional failure (deliberately bad config) lands in Inbox, retry succeeds.

### T06 — Exception: full loop **[S]**

- **Ingress:** explicit "this run is an exception to the normal policy" emitted by an agent or filed by a human.
- **Surface:** Inbox shows the rule it deviates from + reason.
- **Action:** "Accept" creates a named exception with TTL; "Decline" rolls back the action.
- **Side effect:** named exception stored in `policies` (or a new `exceptions` table — depends on `policies.py`).

**Acceptance:** at least one exception filed and one rejected; both leave traceable evidence.

### T07 — Routing rules for the 5 shapes **[S]**

- File: `backend/app/api/v1/routes/inbox_routing.py` and seed bundle.
- Verify default routing rules exist for each shape:
  - clarification → `repo_maintainer` group
  - improvement → `eng_managers` group
  - approval → policy-defined owner
  - failure → `on_call_eng` group
  - exception → `secops` group
- Audit the resolved owners on a fresh workspace.

**Acceptance:** seed bundle creates the 5 default rules; resolution preview shows the right owners.

### T08 — Inbox UI density review **[S]**

- File: `console/src/app/inbox/page.tsx` + `[id]/page.tsx`.
- Triaging 20+ items in one session must work: keyboard shortcuts, bulk actions ("snooze all selected"), filter chips by shape and age.
- Run a UX session against ElMundi data after a few days of accumulation.

**Acceptance:** maintainer can clear an Inbox of 25 items in under 10 minutes.

### T09 — "Healthy quiet vs broken silence" indicator **[S]**

- The blog post warns: a workspace with no Inbox items ever is suspicious.
- Add a small indicator: "Inbox quiet for 7 days" → green if expected, amber if recent failures suggest items should have appeared.

**Acceptance:** indicator implemented, tested on three states (quiet-fresh, quiet-old, busy).

## Definition of done

- [ ] All 5 shapes have an end-to-end recorded loop.
- [ ] Inbox UI handles 25+ items without ergonomic complaints.
- [ ] Default routing rules ship with new workspaces.
- [ ] Healthy-vs-broken-silence indicator live.

## Risks / unknowns

- `services/inbox/dual_write.py` has a TODO for sweeper reconciliation — needs review for races.
- Some side-effects in `services/inbox/side_effects.py` are marker-only (TODO log); productionize the most-used.
- Approval flow for resuming a paused workflow_dispatch is fragile (run_token expiry).

## Out of scope

- Cross-workspace Inbox views.
- Mobile-first Inbox redesign (covered by E12 mobile work).
- AI-assisted Inbox triage / suggested dispositions.
- Custom Inbox shapes beyond the 5 canonical.
