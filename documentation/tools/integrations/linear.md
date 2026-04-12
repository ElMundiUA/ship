# Linear (tracker)

**Role in Ship:** system of record for the delivery queue — states, labels, comments, and evidence the agent and humans share.

## What you wire

- **Projects** — e.g. SDLC lane vs audit boards; keep automation lanes separate so intake never steals audit throughput.
- **States** — map your columns to Ship semantics (`Backlog` → `Todo` → `In Progress` → `In Review` → `Done`, plus `Blocked`). Document the mapping in adoption notes.
- **Labels / fields** — `ready:*`, `stage:*`, `result:*`, QA splits; if you rename, keep the *meaning* stable (see tracker contract below).

## Agent touchpoints

- Pick scripts and workflows assume **machine-readable** issue metadata (GraphQL or equivalent in your adapter).
- Cloud agents may **comment** and **transition** issues only where your policy allows; every move should cite a PR URL, CI run, or test artifact where possible.

## Read next

- [Tracker adaptation contract](/docs/tools/ship-agent-trackers) — vendor-neutral interface your tracker must satisfy.
- [Delivery, quality & release](/docs/adoption/delivery-quality-and-release-process) — gates and evidence habits.
- [Agent launch matrix](/docs/adoption/agent-launch-matrix) — where Linear fits in runtime choices.

For a **reference** cron grid and YAML names (ElMundi-style), see [Examples → ElMundi](/docs/examples/elmundi) — treat names and URLs as templates, not requirements.
