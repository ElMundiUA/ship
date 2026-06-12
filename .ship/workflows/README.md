# Ship workflows — imperative bounded pipelines

A workflow here is a **deterministic, bounded fan-out you INVOKE for
one job** (thesis 8): it fans out N agents, joins, synthesizes, and
**completes**. Specs are YAML (`<name>.yaml`), validated by
`packages/cli/lib/workflow/loadSpec.mjs` (lint) and
`apps/backend/app/services/workflow/spec.py` (authoritative, at
dispatch time). Seven step kinds: `parallel`, `pipeline`, `loop`,
`barrier`, `synthesize`, `judge`, `verify`.

Every leaf spawn passes the **WorkflowDispatchGate**
(`services/workflow/gate.py`): `workflow:<run>:<step>` lease, a
`workflow:`-prefix cap separate from the SDLC ticket cap, cascade
depth counted on recursion edges (`ship` self-spawn leaves / nested
workflows), and a durable per-attempt idempotency row. `max_fanout`
(hard ceiling 8) and `max_depth` are rejected at LOAD time — a
malformed spec can't even ask for a fork-bomb. No autonomy profile
loosens any of this.

## The boundary with /process — do not merge or duplicate

| | `/process` (processes.py) | workflows (this directory) |
|---|---|---|
| nature | **reactive state machine** | **imperative bounded pipeline** |
| lifetime | per-ticket, continuous — waits for events, never "finishes" | one invocation — runs the DAG, returns a result |
| triggers | schedule / event / manual stage transitions | chat (`run_workflow` tool), FSM gate, cron |
| defines | which SDLC stages exist and how tickets move | how N agents fan out for ONE job |

**Rule: do NOT add workflow execution into `processes.py`, and do NOT
add state-machine semantics into the workflow runtime.** They
complement each other: a /process stage (e.g. `code_review`) may FIRE
a workflow as a gate action — that is the only sanctioned coupling.

## Dogfood set

- `pr-review.yaml` — 4 parallel review axes (correctness / security /
  simplification / test-coverage) → barrier → synthesize (structured
  findings with severity) → verify (adversarially re-check the top
  finding). Fired from the `code_review` gate or chat.
- `codebase-audit.yaml` — coding leaf enumerates hotspots → 3
  parallel reasoning audits → judge ranks tech-debt items into a
  structured report. Fired nightly via cron (per-workspace opt-in,
  fail-closed).

The server currently executes the **packaged** copies of these specs
(`apps/backend/app/services/workflow/specs/`); the files here are the
repo-side contract the CLI lints. Keep them in sync.
