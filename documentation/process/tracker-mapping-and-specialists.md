# Tracker mapping and specialists

At runtime a routine answers two questions. *Which tickets are eligible?* — answered by tracker mapping. *What role am I playing?* — answered by the specialist. Get either one wrong and the routine either looks at the wrong tickets or behaves like the wrong role. Both are small panels in [the process editor](/docs/process/editor). Both pay back over months.

## Tracker mapping

The tracker mapping panel binds tracker states to process states. Linear's "In Progress" maps to your process's `in_progress`; Jira's "In Review" maps to `review`; "Done" maps to `done`. The mapping is per-process and per-tracker. Unmapped states surface as warnings — leave one unmapped and routines either skip those tickets entirely or, worse, treat them as eligible when they shouldn't be. The fix is always to map; never to ignore the warning.

A subtlety worth knowing: tracker mappings are not symmetric. A tracker state can map to a process state, but a process state can fire actions that don't change the tracker — a routine might post a comment on a PR without moving the ticket. That asymmetry is intentional; not everything a routine does is a state machine event. Your process drives the logic. The tracker is a mirror.

When you sit down to map your tracker, think in two directions. First, what states does your tracker have, and which process state is each one closest to? A ticket in "Backlog" is not yet `in_progress`. A ticket in "Blocked" might stay `in_progress` in your process, because the routine needs to see it and act on the block. Second, are there process states your routines will reach that the tracker has no equivalent for? A routine might move a ticket to a state you call `awaiting_feedback`, but your tracker only knows "In Progress" and "In Review". Map `awaiting_feedback` to "In Review" if that's the right external signal, or leave it unmapped if the ticket should stay where it is. The system will tell you if you've left gaps.

## Specialists

The specialist catalogue ships with a handful of role profiles, each one a persona with a role description that shapes the model's behaviour during a run:

- **Intake** — clarifies incoming work, checks minimum context, and routes tasks. Front of the funnel.
- **Business analyst** — turns ambiguous requests into requirements and acceptance criteria.
- **Product manager** — clarifies scope, priority, tradeoffs, and launch criteria.
- **Designer** — reviews UX flows, product copy, accessibility intent, and design quality.
- **Technical architect** — plans architecture, migration strategy, boundaries, and technical risks.
- **Developer** — implements code changes, tests, docs, and prepares PRs.

A specialist is a role description, not a unique person. Two routines can take on the same specialist; one specialist can be used across many processes. Custom specialists exist when the team has a workspace-specific role — for example, a compliance reviewer for a regulated vertical. Custom specialists live alongside the catalogue and are versioned the same way. Each specialist has an id, a name, and a role description; the id determines which specialist a routine runs as.

When you choose a specialist for a routine, you are choosing a lens. The intake specialist reads incoming tickets and asks "is there enough context?" The developer specialist reads the same ticket and asks "can I start coding?" A ticket might satisfy both, or satisfy one and not the other. That gap is useful information. If your developer routines are spinning their wheels waiting for clarification, the ticket is reaching them before the intake routine has done its job. If your architects are never consulted before a developer routine starts, your process is missing a handoff.

## Putting them together

A routine fires. It looks at tracker mapping to compute the eligible ticket set — which tickets in which tracker state should this routine see right now? It loads the specialist's role description and uses that as the role-specific context the model acts under. It reads the relevant knowledge buckets, the tracker context, and any other routine-specific input. It produces output: a comment, an Inbox item, a PR, a knowledge note. Every step is recorded; nothing is invented from nothing.

The combination is precise. A developer routine filtering for `in_progress` tickets with the developer specialist will look at different tickets and behave differently than an architect routine filtering for the same tickets with the technical architect specialist. Both see the same ticket; both have the full context. One is asking "how do I build this?" The other is asking "is this buildable the way it's designed?" One produces code commits and PR feedback. One produces architecture notes and risk assessments. Both are right. Both are necessary.

Tracker mapping is plumbing — keep it boring and keep it complete. Your system should have no unmapped states surfacing warnings. Specialist choice is design — it's worth thinking about whether a routine should run as a developer or as an architect, because the answer changes what the routine produces. Get both right, and your routines see the work they own and act with the clarity of a person who knows their role.
