---
title: The agent that finished without committing
slug: the-agent-that-finished-without-committing
date: 2026-05-01
kicker: Field note
description: A real intake agent did the right work, in the wrong shape — it answered the ticket but never pushed a branch. The old exit protocol assumed every agent writes code. Branchless agents had nothing to commit, so we replaced the file contract with an HTTP one.
reading_time: 7
author: Denys Kuzin
tags: [agents, protocol, build-in-public, autopsy]
---

This is a story about a contract that was wrong because it assumed everyone holds the same kind of pen.

It is also a small one. The change is one commit, one new endpoint, four removed helpers. The lesson is bigger than the diff. We had been treating "the agent finished" and "the agent pushed code" as the same event. They are not. Today the intake agent on ELS-27 finished cleanly without pushing anything, and the rest of the system had no language for that.

## What the intake agent did

ELS-27 is a Linear ticket asking for richer label namespaces in the Ship adapter — `ready:*` next to the existing `stage:*`, and a `needs:clarification` for tickets waiting on a human answer. We routed the ticket through the `task_intake` FSM stage. The intake agent's job is to read the request, decide whether it is a bug / feature / improvement, and either pass it to the next stage or come back with questions.

It came back with questions.

The Linear comment, posted at 22:03 UTC by the agent's session, did the right things: classified the ticket as an improvement, named the four ambiguities a human had to resolve before architecture could happen, and asked for `needs:clarification` to be applied while the conversation runs. It even called out, correctly, that the Linear MCP it was looking through could not see ELS-27 from where it was standing — a different workspace.

That is exactly the output we want from an intake pass that hits ambiguity.

The Cursor cloud agent ended its run with `status=FINISHED`. No branch was pushed. No `.ship/run-state.json` showed up anywhere. The CLI on our side polled the agent, read the terminal status, and exited with no idea what just happened.

## The contract that was wrong

The exit protocol the agent had been given said: when you are done, commit a single file at `.ship/run-state.json` to your branch and stop. Ship CLI reads that file and applies the writes. It is a tidy shape on paper. It has one assumption baked in.

Every agent has a branch.

That assumption holds for the developer role and the QA role, both of which change code by definition. It does not hold for intake, business analysis, planning, architecture review, dependency mapping, or any of the other passes a Ship workspace runs that look at a ticket and produce a comment. Those agents have nothing code-shaped to commit. Forcing them to push an empty branch carrying a state file was an artefact of the contract, not a property of the work.

The intake agent on ELS-27, sensibly, refused. It did its job, posted a comment via the only writable surface it had — the Linear MCP — and walked away from a contract that was asking it to commit nothing twice.

We had two options. Make every agent commit a placeholder, or let the contract take the shape of the work.

> A protocol is wrong when it asks the work to lie about its shape. A real intake pass produces a comment, not a commit.

## What changed

The new contract is an HTTP call. When an agent finishes, it POSTs to a single endpoint — `POST /v1/workspaces/{ws}/agent-runs/finish` — with its outcome, the run id Ship CLI minted before launching it, and any payload the outcome implies. Ship's server applies the tracker side-effects through the workspace's existing OAuth, the same way it always did. The shape of the call replaces the shape of the file.

There are four outcomes.

`ready_next_step` says the role finished cleanly. The payload names the next FSM stage, optionally a comment to post, and the server transitions the ticket. This is what `developer` and `qa_manual` use; it is also what `intake` uses on a non-ambiguous ticket.

`needs_clarification` says the agent has a question that has to be answered by a human before automation can move further. The server applies a `needs:clarification` label so the intake stage stops re-picking the ticket, optionally posts the comment carrying the question, and drops a clarification row into the workspace inbox so an operator notices. The ticket stays in Todo. This is what intake on ELS-27 should have called.

`blocked` says the agent could not proceed because something in the environment is wrong — a missing secret, a broken adapter, a conflicting branch. The server drops a blocker into the inbox; the ticket itself is unchanged. This is the difference between "I cannot do this" and "I have a question."

`out_of_scope` says the ticket is not a real ticket. The server moves it to Done with whatever comment the agent left.

Branchless agents call this endpoint and stop. Branchful agents push their code on the branch the CLI named for them, then call this endpoint. The branch is where the code lives. The endpoint is where the outcome is reported.

Idempotency is one column wide. The endpoint stores the run_id in the audit log; a second call with the same run_id returns a `duplicate_ignored` no-op so the network retries inside Cursor never double-write.

## What it cost to change

Eight files. Four hundred ninety-one lines added, two hundred seventy-six removed. The CLI lost a hundred lines because it no longer fetches a branch from GitHub or parses a state file or knows about three different write endpoints; it just launches the agent and exits when Cursor says terminal. The pattern's exit-protocol section got shorter by half because most of the verbosity was about the JSON shape of a file that no longer exists.

The Linear adapter grew one method — `add_signal_label` — and one filter clause. The `_fsm_filter` for every stage now excludes tickets carrying `needs:clarification`, so an intake question hangs the ticket on the human's desk instead of the agent's. The provisioner picked up a `SIGNAL_LABELS` registry and ensures `needs:clarification` exists at OAuth time. The live ELShip integration was backfilled with the new label without another OAuth dance.

The bundle bumped from 0.9 to 0.10. Pattern hashes restamped. The CI guard for `content_sha256` will refuse the PR if anyone forgets that step.

## What the wrong contract was costing us

We were lucky on ELS-27 because the Cursor agent had a Linear MCP available to it and used it to write the comment. Without that MCP, the agent's classification and four questions would have been noise — generated and lost. Either way, the result was the same to Ship's pipeline: a `FINISHED` status with no signal, a CLI exit with no audit trail, a ticket sitting unchanged with an undocumented comment from a workspace member.

The shape of the wrong contract was teaching the agent to either lie (commit a placeholder file, push an empty branch) or escape (write directly to the tracker, bypass the pipeline). Neither of those is a behaviour we want to scale to the next twelve agent roles. The new contract gives the agent a way to say what it actually did, in the only language Ship's server can hear, and stops asking it to perform a code change it never made.

> The shortest path between an agent's output and a tracker mutation is the one that does not pretend the agent built something it didn't.

The next intake run, on the next Linear ticket carrying ambiguity, will call `/agent-runs/finish` with `outcome=needs_clarification`. The label will land. The intake stage will stop re-picking. The operator will see a clarification row in the inbox. The audit log will record the run_id and the outcome. A human will answer. The label will come off. The intake stage will pick it up again, this time without ambiguity, and the FSM will keep moving.

That is what we wanted from intake all along. We just had to stop asking it to push a branch first.

## The principle

Two things to take out of this, neither one new — both old enough to keep getting forgotten.

Let the work define the protocol. We wrote the file contract first, when the only agents we had ran were code-changing agents, and we wrote it for the role we were watching. The minute we added a role whose work was a comment and not a commit, the file became overhead. Branchless agents are not edge cases; they are most of the agents in the Ship FSM. The protocol was wrong because it codified the loudest role's shape.

Make the failure mode honest. A branchless agent forced into a file contract has two ways to fail: produce a placeholder commit (lie), or write directly to the tracker (escape). Both of those are worse than what we have now, which is an HTTP call that the audit log either contains or does not. If the agent skips the call, the absence is observable. If the agent does the call, the side-effects are routed. There is no third path where the work happens but the system can't tell.

The Sunday before this one, we wrote a post about writing the protocol before the product. This is the small companion to that post: when the protocol meets a real run, the run wins.

We will write it again next week, with whatever we get wrong next.
