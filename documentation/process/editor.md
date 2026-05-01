# Reading the process editor

The process editor is opinionated about safety. You see what's there, you propose changes, you review the diff before saving. It's modeled after code review, not after a free-form configuration screen. When you open a process at `/process/<processId>`, the interface shows you the full workflow graph, all the routines attached to it, and how tracker states bind to process states. Every change you make is staged; nothing is live until you confirm the diff and save. The editor is your co-pilot for process design, not a free-form form.

## States

The states column shows the workflow columns: each state has an id, a human label, the specialist that owns work in that state, and the capacity—how many items are healthy in that state at once. When you edit a state, you are usually just renaming the label or adjusting the description; the id stays fixed so that transitions, routines, and tracker mappings keep pointing at the right place. Reordering states needs careful thought because transitions reference state ids by position and name. If you add a new state, it opens as "draft" until you connect it with at least one incoming transition; the editor will not let you save a state that work cannot reach.

## Transitions

Transitions are the arrows between states. Each transition has a from-state, a to-state, and a set of conditions—typically something like "only when an Inbox approval is resolved" or "only when the tracker state is Closed." The editor draws the graph visually so you can see the flow at a glance. If you remove a state's only inbound transition, the editor warns you that work cannot reach that state; you must either restore the transition, delete the state, or add a new path in. Conditions are optional, but leaving them off means work will move forward unconditionally whenever the specialist triggers the transition.

## Routines panel

The routines panel lists every routine attached to the process: the routine id, its schedule (cron expression or event-driven trigger), the specialist it runs as, and the prompt template. Routines from the [routine catalogue](/docs/process/routines) come pre-attached to certain processes; you can add custom routines or remove ones that are no longer needed. Inside the panel, the "Run now" button triggers a manual execution of that routine immediately, useful for testing a new prompt or recovering from a stalled state. The "View runs" link opens the pipeline history so you can see logs, outputs, and error traces from past executions.

## Tracker mapping

The tracker mapping panel binds tracker states—the columns in Linear, Jira, or whatever issue tracker you use—to process states. Without a mapping, the engine doesn't know that "In Progress" in your tracker corresponds to "in_progress" in your process, and items will not move through the workflow correctly. The panel surfaces unmapped tracker states with a clear warning; if you leave a state unmapped, items will skip the process entirely. Setting up the mapping is straightforward: pick the tracker state, select the process state it maps to, and save. Most setups map five to eight states.

## Schedule and review

The flow schedule panel surfaces every cron-driven routine in one place, arranged by time. It is useful for spotting overlapping schedules—routines that would run at the same moment and compete for database locks—or routines that haven't been touched in months and may need review. When you save a process change, the editor opens a confirmation view that summarizes exactly what is changing: which states were renamed, which transitions were added or removed, which routines were swapped out. The change goes through the same review path as any configuration; the changes are persisted to your repo's `.ship/config.yml` file (the `process` block; legacy code refers to this as the `lanes` block).

---

Most edits to a process are small: adjust the capacity of a busy state, swap a specialist on a routine, or fix a tracker mapping that is causing items to slip. Process redesigns—splitting a state into two, rerouting the entire flow—are rare and deserve a real conversation with the team. Treat them like a schema change in a database: draft it, review it, and make sure everyone understands the migration path for work already in flight.
