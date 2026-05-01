# Disposition: the action vocabulary

Every item in your Inbox ends somewhere. It moves from `new` to one of a finite set of resolved states, and along the way, someone makes a decision: to answer, accept, approve, retry, acknowledge. That decision is recorded with a timestamp, a name, and ideally a reason. The action set is deliberately closed — you cannot invent a sixth verb — because the set of real decisions on real work is actually small. When you find yourself reaching for an action that doesn't fit cleanly, you've probably bumped into a new *kind* of item, not a new way to close an old kind.

## Type-specific actions

Each item kind has exactly one primary path to resolution — sometimes two that branch on the same decision.

A clarification is closed by **answering** it. The answer payload becomes part of the routine's next step; Ship captures the answer and threads it into execution. There is no "dismiss a clarification" — if you choose not to answer, you snooze it with a condition or dismiss it with a reason. Both are honest moves. An answer is not an opinion; it's a fact you've verified, looked up, or decided.

An improvement is closed by **accepting** it. You can also decline it or defer it by **dismissing** it with a reason — "deferred until after the Q2 roadmap" or "out of scope for this product" — and that dismissal is evidence. Dismissed improvements don't disappear; they age in the audit log. The honest versions are the ones that survive reading six weeks later.

An approval is closed by **approving** or **rejecting** it. Both are real answers. Both leave evidence. A rejected approval is not a failure; it's a completed decision. The reason matters: "rejected — missing security sign-off" tells the next reader why the decision stands. "Rejected" without a reason is a papercut that becomes a mystery.

A failure is closed by **retrying** the action it represents. But the retry button is not a vending machine. Check the failure's evidence first — the error log, the stack trace, the external system's response — and make a human judgment. A retry that ignores the failure's root cause is just optimism on a loop.

An exception is closed by **acknowledging** it. An acknowledged exception is on record. An unacknowledged exception drifts into folklore, and the next person hits the same wall without knowing the wall exists.

## Cross-cutting actions

Three actions apply across all item kinds: **snooze**, **dismiss**, and **reassign**.

Snoozing parks an item with an explicit wake condition — typically a calendar date or a state change in another item. "Snooze until April 15" or "snooze until LIN-447 closes" are real conditions. Snoozed items reappear when the condition is met. A subtle point: snoozing an item without a real condition is dismissing it badly. If you're not going to act, dismiss it and write the reason. Snoozing forever is the same as dismissing forever, except it takes up amnesia space in your inbox.

Dismissing closes an item without resolving it. The original concern stands — the clarification stays unanswered, the improvement stays unbuilt, the approval stays unsigned — but you've decided not to act on it right now, possibly ever. Dismissal requires a reason. "Out of scope," "waiting on dependent work," "known limitation, accepted" — these are the dismiss reasons that age well. "Just not feeling it" is honest but doesn't help the next reader.

Reassigning re-runs the routing logic for a single item with a manual override. The underlying routing rules don't change — only this one item is rerouted to a different owner. This is useful when the routing rule got it wrong for an edge case, or when a domain expert is only temporarily unavailable and you know who should handle it in the meantime. See [the routing rules chapter](/docs/inbox/routing-rules) for how routing works.

## Why we record the why

Every disposition can carry a short reason. The reason is not for Ship — Ship doesn't read it — but for the next person who looks at the audit log six weeks from now. That person might be you. They might be someone onboarding to your team. They might be an investigator for why a critical approval was rejected, or why a recurring clarification came in three times before someone decided to answer it once and write a knowledge article.

A one-line reason is the difference between an item that ages well and an item that ages into a mystery. "Deferred — pending LIN-447 closure" is ten seconds to write and saves hours of confusion later. "Rejected — missing security review from @alice" is evidence that the decision was made with rigor, not caprice. "Dismissed — this behavior is actually correct; documented in handbook section 4.2" is a gift to the next reader, and a gift to the person who filed the item if they open the resolved log weeks later and see why their concern was addressed.

## Closure

The action vocabulary is small on purpose. Every closed item should map cleanly to one of these verbs: answer, accept, approve, reject, retry, acknowledge, snooze, dismiss, reassign. If you find yourself inventing a seventh action or stretching one of these verbs into an unnatural shape, you've probably identified a new *kind* of item. That's not a problem with the action system — that's a conversation about the type taxonomy. But it's a conversation worth having in the open, not buried in a workaround.
