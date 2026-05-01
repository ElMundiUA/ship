# The routine catalogue and standalone routines

A routine is a named job with a prompt and a schedule. Some are conservative—read-only sweeps that produce a digest. Some open Inbox items. None of them merge code without a human approval somewhere in the chain. The catalogue gives you the common ones for free; standalone and custom routines extend the surface. When you first set up Ship, you have a choice: enable the routines that matter to your team, disable the rest, and add custom ones as you discover workspace-specific needs.

## Shipped catalogue routines

A small set of routines is shipped pre-configured. Each has a default prompt and a default cron, but you can adjust the schedule, change the specialist, or modify the prompt—the id and the basic shape are fixed by the catalogue, which means you can opt in across many workspaces without re-explaining what each one is for.

- **Daily security review** (`daily_security_review`): scans dependencies and secrets policy on a daily cron; lists actionable security follow-ups.
- **Technical architecture review** (`daily_technical_architecture_review`): checks architecture and API boundaries; flags drift, coupling, and migration risks. Default cron on Mondays.
- **Architecture tests review** (`daily_architecture_tests_review`): recurring check on test architecture and coverage signals; weekday cron.
- **Daily digest** (`daily_digest`): consolidates in-flight work, blockers, and risks for the team; weekday morning cron.

These are not the only catalogue routines; we publish more as patterns stabilize. The point is that they ship with sensible defaults, so a team can run them in closed beta and tune them without starting from a blank prompt. Each catalogue routine has a `specialist` field—the role running the job. For the security review, that specialist is a security expert with access to dependency trees and policy. For the digest, it's a team coordinator who knows what "blocker" means in your context.

## Schedule shapes

Three kinds of schedules: **cron-driven**—a calendar trigger like `0 8 * * 1-5` for weekday mornings, **event-driven**—a tracker state transition, a PR opened, a push to main, or **manual**—run by hand from the console for one-off work or testing. Most catalogue routines are cron-driven; custom routines can pick whichever shape fits. An event-driven routine might open an Inbox item the moment a security vulnerability lands in a notification. A manual routine is useful for one-time scans or for testing a new prompt before you schedule it. Cron-driven routines let you batch work into quiet hours or windows aligned with your team's natural rhythm.

The choice of schedule shape reflects the urgency and frequency of the concern. If you care about something every Monday morning, cron is right. If you care about something the instant it happens, event-driven makes sense. If you're still figuring out what to ask, manual runs let you iterate without clock pressure.

## Standalone routines

A standalone routine is not attached to a process—it runs at the workspace level. Right answer for cross-repo sweeps (a workspace-wide weekly digest of test flake, a security scan across all dependencies, a review of newly retired vendor integrations), or for routines that don't care about per-repo state. Standalone routines still have a specialist, still produce evidence, and still surface failures in the Inbox. The difference is scope: a process-level routine cares about one repository's state; a standalone routine sees the whole workspace and can roll up findings across repos, teams, or systems.

A common pattern: ship a catalogue routine as standalone (e.g., the daily security review), then teams that need something tighter at the repo level add a custom sibling routine attached to the process. Both feed the same Inbox, both have the same failure semantics, but the scope is different.

## Custom routines

A custom routine is one you author yourself. The minimum: an id, a name, a prompt, a default schedule, a specialist. The more interesting question is *why*: most teams discover that the catalogue covers the obvious cases, and a custom routine is needed for a workspace-specific concern. Maybe you integrate with a particular vendor and need a daily check that their credentials are still valid. Maybe you maintain a customer-specific runbook and want an automated walk-through on Fridays to catch stale links or outdated examples. Maybe you run a financial system and need a monthly reconciliation sweep. Custom routines live in the same panel as catalogue ones; the difference is that you maintain the prompt as part of your repo config.

When you author a custom routine, start with a prompt that is narrow and specific. A routine that tries to do ten things produces noise; a routine that does one thing well earns its cron slot. If you find yourself explaining the routine's output to the team every time it runs, the prompt probably needs tightening. If the routine runs and produces nothing most days but finds something occasionally, that is not a problem—that is a valid answer. An empty run means nothing was eligible, and that is fine.

## When a routine is misbehaving

A routine that fails three times in a row produces a failure Inbox item. A routine that succeeds but produces nothing—empty runs—is fine; that is a valid answer and means the check ran cleanly with no findings. A routine that produces noise every day is a different problem: it is optimizing for motion, not outcomes. The fix is usually to tighten the prompt, narrow the scope, or change the schedule, not to add a sixth retry. Review what the routine is actually finding and ask whether it matters to the team. If it does, the prompt may be too broad. If it does not, the routine may not be earning its keep.

If a routine starts behaving differently—failing when it used to succeed, or finding different things—check whether something in your codebase or environment changed, and whether the specialist's context is still accurate. A routine that checks architecture coupling needs to know what your architecture looks like; if you just merged a big refactor, the routine's findings may spike temporarily. A routine that scans for secrets policy violations may start finding things if your policy definition changed. For more on healthy and unhealthy routines, see [healthy and unhealthy routines](/docs/process/health).

The catalogue is a starting point, not a ceiling. Most workspaces use four to six catalogue routines and add one or two custom ones; if you find yourself with fifteen routines competing for attention, the question is rarely "do we need more routines?"—it is usually "which ones are still earning their keep?" Disable the ones that have stopped being useful, refactor the ones that have become noisy, and add custom ones only when you have identified a gap that the catalogue does not cover.
