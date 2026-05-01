# The morning loop

This chapter is the operating reference for experienced Ship users: the order of moves to make in a normal morning, with cross-links to where the detail lives. It differs from [A day in Ship](/docs/orientation/a-day-in-ship), which walks a new user through their first morning step by step. Here we assume you know the interface and we focus on *why* the order matters: health before decisions, decisions before review, review before drift, drift before audit.

## Workspace health

Open the workspace home. You are looking for three things: a banner about an out-of-date Ship template bundle on a connected repo, system status indicators that are not green, and process or testing signals reporting repeat failures. If a bundle banner is present, open the wizard, review the proposed changes, and merge the PR—this typically takes two minutes and prevents downstream friction. Record any other concern as an Inbox item or a tracker ticket, but don't troubleshoot in the morning loop. The pattern is note and move on. A red system status is a signal to check your audit log before making changes; a pattern of repeated test failures is a signal that something upstream has drifted.

## Inbox

Drain it. The triage order is failures and approvals first because they block other work; clarifications next because answering one often unblocks many; improvements you can defer with a clear reason; exceptions need an owner and an acknowledgement. Most mornings this takes five to fifteen minutes. For the action vocabulary and disposition patterns, see [the Inbox disposition guide](/docs/inbox/disposition).

## Shipped and in-progress

Glance at recently shipped. Did anything merge in the last 24 hours? If so, spot-check the evidence: is there a tracker link, a PR, clean checks, and a reviewer name? This is not paranoia; it is the small signal that the pipeline is working. Then look at work-in-progress. Every active item should have tracker context, repo context, and a named owner. An item that has been "in progress" longer than the work itself warrants is an Inbox item waiting to happen—bring it forward now rather than waiting for it to become a problem.

## Knowledge drift

If a clarification repeated this week, or a routine emitted the same finding twice, the root cause is not that you are slow at responding; it is that the knowledge base is incomplete. The fix is upstream: a knowledge article in the right bucket, reviewed and published, not a faster typing speed. This is the difference between scaling and staying tired. See [the chapter on imports and review](/docs/knowledge/distiller-and-review) for how knowledge flows into the system.

## Audit when something feels off

You do not open the audit log every morning as a habit. You open it when an instinct says something happened that should not have, or did not happen that should have. The log records privileged actions: who did it, what was the target, when, and what changed. It is the receipt for everything else in the loop. When you find an unexpected action, the entry tells you exactly what happened and who caused it.

---

Do not optimize for PR counts, agent action counts, or Inbox volume. Counts are the easy chart and the wrong chart—they reward motion, not direction. A quiet morning with one high-value change shipped is better than a busy morning with six items half-done. See [the loud demo trap](/book#the-loud-demo-trap) in the book for why.
