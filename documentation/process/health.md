# Healthy and unhealthy routines

The difference between routines that earn their keep and routines that are vanity throughput is rarely visible in a chart. Both light up dashboards. Both send notifications. Both record runs in an audit log. The distinction surfaces only when you ask a hard question: would you defend this in a postmortem? A healthy routine can be explained, audited, and retracted. An unhealthy routine hides behind motion and notifications until someone has to live with the consequences.

This chapter walks through the common failure modes that trap teams, the field-tested cures, and the test the book teaches.

## 1. Empty runs are valid

A routine that runs and finds nothing eligible is not broken. It is the routine telling you what is true: nothing met the criteria today. Empty runs are recorded with a "no eligible work" status. They are valid data.

The temptation is to optimise the criteria so something always fires. Add a broader filter. Lower a threshold. Adjust the queue query. The result feels productive because the calendar is full and the notifications are loud. This is the same shape as a vending machine for activity — it moves because it was engineered to move, not because anyone was hungry.

The cure is to leave empty runs alone and look at what they reveal. Sometimes "nothing eligible" is itself an alarm. The criteria are too narrow. The tracker is misconfigured. The product has drifted away from what the routine expects to find. And sometimes it is the system working exactly as designed, and silence is the right answer. The distinction matters. You will not see it if you are chasing the urge to always have something to show.

## 2. Hero agents

Two routines believe they own the same ticket, or two specialists are emitting comments on the same PR. The result feels productive because the terminal is full and the notifications are loud. The humans are getting updates. The board is moving.

In practice you get duplicate work, contention, and humans who learn to distrust anything with an automated author. You merge the same fix twice. You revert overlapping changes. You read conflicting advice from two agents who have no idea the other is talking. The audit log shows both ran successfully. The postmortem shows nobody owned the surface.

The cure is one delivery role per window. Only one specialist owns a state at a time. If routines touch the same surface — the same ticket, the same PR, the same queue — they need explicit coordination. Not concurrent ambition. Not a silent race to be first. Coordination. Decide in advance who moves when. Use guards, ownership markers, and state checks so that only one agent claims a piece of work.

## 3. Vanity throughput

A dashboard that celebrates "PRs opened" or "comments posted" by routines is rewarding the wrong thing. Throughput is cheap. A routine can open twenty PRs in an hour. Aligned outcomes are expensive. Aligned outcomes require the right change, attached to the right ticket, reviewed by the right person, merged without surprise.

We refuse vanity throughput — the metric that glows when you ran the agent four hundred times and says nothing about how many outcomes were mergeable, auditable, and intended. Motion is cheap; aligned outcomes are expensive. Dashboards that reward touches teach the organisation to be busy on purpose, because busy reads as commitment to people who do not have to merge the result.

The cure is accountability metrics. Did the change match the ticket? Did the evidence get attached? Did a human approve where a human should approve? Count those. Quietly retire the throughput chart that nobody can defend. Track outcomes, not touches.

## 4. Drifted prompts

A routine's prompt was last edited eight months ago. The product has shifted. The team's ontology has changed. The prompt now describes a world that does not exist. The routine still runs. It still produces output. But the output is increasingly misaligned with what you need, and you do not notice until the output has already been merged.

Drifted prompts are the routine equivalent of code that was never reviewed after the first draft. Except worse: code drifts slowly, and humans read the diff. Prompts drift invisibly because humans do not see the diff until the output is wrong.

The cure is treating prompts like code. Version them. Review them when they change. Date them. Use the [authoring guide](/docs/developer/authoring) when you edit one. Prompt configs live in `.ship/config.yml`, versioned in git. If a prompt is in a SaaS text box and not in git, that is the failure mode, not the workaround. Keep your prompts in the same version-control discipline as your source code.

## 5. Routines that quietly do nothing

A scheduled routine that exits early because of a misread queue delay. A guard that was right on the whiteboard and wrong against real platform timing. A specialist that was built to handle one class of work and now that class has moved upstream. The symptom is a routine that succeeds in the audit log but produces nothing, week after week.

Success and silence together are a loud alarm. You do not notice because both read as "working." The routine did not crash. It did not error. It just found nothing to do, and you did not ask why.

The cure is checking *absence* on a regular cadence. A routine that has produced no output in two weeks is either obsolete or broken. Either answer is worth knowing. Build a habit of asking: when did this last do work? If the answer is "I do not know" or "never," you have a routine that is not paying for itself.

## 6. Long Todo columns of routines

A workspace with seventeen routines is rarely a workspace running seventeen useful routines. It is usually a workspace where someone added a routine without retiring an older one. The routines are stacked up like clothes in a drawer. Nobody knows which ones are still dressed in. Nobody knows which ones have been superseded by product changes or merged into another routine.

The cure is the same as for code: a routine without a clear owner and a recent meaningful run is debt. Retire it. Delete it from the config. Archive the prompt. The fewer-but-stronger workspace beats the many-but-noisy one. A routine that is ambiguous about its purpose or ownership is worse than no routine.

---

The postmortem test separates healthy routines from healthy-sounding routines. When something goes wrong, can you point at the routine, the prompt version, the specialist, the inputs, the evidence, the human who approved it (or chose not to)—without a séance? Can you run a query in [the audit log chapter](/docs/operating/audit-log) and explain what happened? If yes, the routine is healthy regardless of what the dashboard says. If no, the dashboard is lying to you. When you find yourself explaining what *should* have happened because what *did* happen is not recorded, you have found an unhealthy routine.

The standard is not perfection. The standard is accountability. Start there, and the rest of the cures follow.

For deeper context on designing reliable routines, see [the prologue](/book#prologue).
