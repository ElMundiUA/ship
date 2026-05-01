# Authoring patterns and policies

Most teams start by adopting the shipped catalogue. Then a workspace-specific concern surfaces — a vendor-specific check, a customer-specific runbook, a regulatory rule that applies across every routine — and the cheap way out is to write a custom artefact. The hard part is knowing whether the right artefact is a pattern (a versioned routine), a knowledge article (a fact agents read), or a policy (a workspace-wide standing rule). This chapter walks you through when authoring earns its keep, the small loop that works, and where the deep technical reference lives. For schema-heavy decisions, the full reference is at [Authoring (reference)](/docs/authoring).

## When to author

Author a custom **pattern** when the same routine logic would live across multiple processes and you want it versioned. Don't author a pattern for a one-off — that's a routine config edit, not a new artefact.

Author a **knowledge article** when the answer is reusable context — a fact, a rule, a runbook. Knowledge is read by agents and reviewers; it doesn't change behaviour by itself.

Author a **policy** (see [the policies chapter](/docs/policies/policies)) when the rule applies workspace-wide and should be in every agent's system prompt regardless of role.

The cheapest mistake is writing a pattern when the right answer was a knowledge article; the second-cheapest is the inverse. If you can't decide, draft both and see which reads cleaner.

## The small loop

The loop is the same one the book argues for: small change, real task, observe, adjust.

1. Draft the artefact (pattern, knowledge, or policy) as a one-page Markdown file.
2. Run it on a real task in a sandbox repo or behind a feature flag.
3. Observe what the agent did with it. Did the routine produce evidence? Did the prompt drift? Did the policy fire when expected?
4. Adjust the wording. Most patterns improve more from removing sentences than from adding them.
5. Once it survives three real tasks, promote it.

The temptation is to author a long, comprehensive artefact and ship it once. The book's instinct: short artefacts age better. Long ones become museums.

## Pattern vs. knowledge vs. policy

Three short tests:

- **Will agents follow steps from this?** → pattern (a routine reads it as a procedure).
- **Will agents lean on this for context?** → knowledge (the resolver injects it when relevant).
- **Should every agent know this regardless of role?** → policy (the system prompt always carries it).

When two answers are yes, prefer the narrower one. A pattern that is also "general standing rule" is rarely both — split it.

## Where the deep reference lives

The schema-heavy reference is at [Authoring (reference)](/docs/authoring). It covers folder layout under `artifacts/`, frontmatter shape, hashing, RFC-0005 (artifact folder spec), and RFC-0004 (adapters). When you are deciding *whether* to author, this chapter is enough. When you are deciding *exactly what frontmatter fields to set*, read the reference.

---

Authoring is how a workspace teaches Ship what is true at this team. The cheap habit is to draft small and iterate; the expensive habit is to author the perfect long artefact and discover it disagrees with reality on the first run. Your first version will be wrong in ways you cannot predict from a whiteboard. That is normal. What matters is that you treat patterns, knowledge, and policies as things you revise on evidence, not things you declare finished because the document is long. For more on policies specifically, see [the policies chapter](/docs/policies/policies); for the full schema reference, see [Authoring (reference)](/docs/authoring).
