# Policies

Prompts go stale, and prompts written by individual specialists drift from each other over time. Policies are the layer above — workspace-wide standing rules that every agent reads regardless of role. When a correction shows up three times across different specialist prompts, or when a regulatory constraint has to apply everywhere, or when an Inbox failure traces back to "the agent didn't know it shouldn't do that," a policy is the answer. Policies let you get opinionated about safety, scope, and tone without baking those things into individual prompts. They live at Settings → Policies, they are admin-only, and they get injected verbatim into every agent's system prompt before the first run. The book chapter [The Wall of Rules Before the First Run](/book#chapter-8-the-wall-of-rules-before-the-first-run) argues for why this layer exists; this chapter covers how to author and maintain policies in practice.

## What a policy is

A policy has four fields: a title, a body written in Markdown, a sort order (lower numbers appear first), and an enabled flag. The body is injected verbatim into the agent's system prompt, so every agent reads it before taking any action. The title becomes an H2 heading in the rendered preamble, so agents and humans scanning the rules can navigate quickly. The sort order convention is to put non-negotiables first — safety rules, escalation thresholds, regulatory constraints — and stylistic guidance last. When you disable a policy instead of deleting it, you preserve the audit trail. If an agent references a rule in a postmortem, you can go back and see when it was active and when it changed.

The form is small on purpose. A good policy fits on one screen and reads like a standing instruction, not a committee memo. A policy that grew to four screens is usually two policies pretending to be one, and the cure is to split it at the natural boundary.

## When to write one

Write a policy when the same correction shows up in three different specialist prompts. Write a policy when a recurring Inbox failure traces back to "the agent didn't know it shouldn't do that." Write a policy when a regulatory or compliance constraint applies across every routine and every role. Write a policy when new team members keep asking the same question about scope or safety in their first week. Don't write a policy for a single routine's quirk — that belongs in the routine's own prompt. Don't write a policy for a draft of an idea the team wants to "try out" — use knowledge or a temporary routine note instead. And don't write a policy for standing context that is true everywhere but that nobody actually wants to enforce — that is knowledge, not policy.

## Authoring a policy

Start by drafting the policy as a knowledge article. Share it for review and let the team argue about the wording before you promote it to the system prompt. The expensive habit is authoring a policy live in the form and re-editing it three times after the first agent run reveals what you meant. The cheap habit is the dry run first.

The body is plain English with examples only when they reduce ambiguity. Use the same Markdown conventions you would use in a knowledge article. Be direct: "always X", "never Y", "when Z happens, escalate to the owner." Reference other policies or knowledge articles if the context helps, but keep the policy itself self-contained. If you need to define a term, do it in the first sentence where it appears. When you update a policy, consider whether the change is urgent enough to warrant a new run of affected agents, or whether it can wait for the next scheduled execution.

## When to update or retire

Policies are reviewed quarterly in mature workspaces and every time someone asks "wait, is that still our rule?" in young ones. Disable rather than delete — the disabled flag preserves the audit trail and lets you search the history. A policy nobody can defend in a postmortem is debt. The cure is the same as for code: retire it when it stops being true. If a policy was written to enforce a constraint that changed, or if it defended against a risk the team no longer worries about, document why it is disabled and move on.

## What does NOT belong in a policy

Don't write a policy that is just a list of every team's preferences. Don't draft an idea someone wants to "try" and call it a policy — use knowledge or a temporary note instead. Don't put specific routine logic in a policy; that belongs in the routine's own prompt. Never store secrets or credentials in a policy. And don't write a policy for standing context that is true everywhere but that the team doesn't actually want to enforce — that is knowledge, not policy. The test is simple: would you be willing to enforce this on every routine, including the ones you haven't written yet? If yes, it is a policy. If no, it is something else.

The next chapter covers how to manage secrets safely and securely across your workspace. Read [Secrets](/docs/policies/secrets) for guidance on storing and injecting credentials that are too sensitive for a policy.
