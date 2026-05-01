# The evidence checklist

A claim without evidence is opinion. In Ship, every important action — a routine that ran, a clarification that was answered, a PR that merged, a policy that changed — should leave a trail that someone unfamiliar with the work could read in five minutes and understand. The evidence checklist is how you make sure that trail exists before it becomes folklore. It is the difference between a team that can say "we decided" and a team that can only say "something happened."

## What counts as evidence

Evidence is anything that can be linked. A tracker issue connecting the action to product intent. A pull request with a clean title and body explaining what changed and why. A CI run attached to that PR, showing green or red. A comment from a human — an approver's reasoning, a reviewer's note, a decision recorded in conversation. A knowledge article that explains the constraint or policy that applied at the time. An audit row showing who did what, when, with the structured payload of the change. Each of these is a fact; together, they form a story.

The key word is "linkable." If you cannot paste the URL into Slack and have someone land on the evidence without bouncing through three other systems, it is not evidence yet. It is a memory.

## Where each kind lives

Tracker evidence lives in your bound tracker — Linear, Jira, whatever system holds your product intent. Pull requests and CI runs live on your code host, usually GitHub. Comments live wherever the conversation happened: the PR, the tracker, sometimes an Inbox item. Knowledge articles live at `/knowledge`, where they can be versioned and cited. Audit evidence lives at `/audit` in the console, with stable rows that show who made the change, when, and what they changed.

Each kind has a home and a URL. Tracker issues are at your tracker domain. PRs are at github.com/owner/repo/pull/number. Workflow runs are at github.com/owner/repo/actions/runs/number. Knowledge articles are at your Ship console under `/knowledge`. Audit rows are queryable at `/audit` with a timestamp and actor. If you cannot complete a full URL for a piece of evidence, it does not yet exist as evidence.

## Checking the trail

Pick a recent action — a PR merged this morning, a routine that ran last night, a policy change from yesterday. Ask the five questions:

1. Which tracker item or product decision started this?
2. Which repo and PR carry the change?
3. Which checks ran, and what did they say?
4. Which human approved, rejected, or deferred the result?
5. Which knowledge article or policy explains the constraint?

If all five answers point to a real link, the trail is solid. If even one answer is "I think it was…" or "let me check Slack," the trail is too weak. Fix it now while the context is fresh. Add the missing comment to the PR. Link the tracker issue in the description. Write the knowledge article that explains the constraint. Cross-reference the audit row in the comment. Do it while you remember why the decision mattered; do not wait until someone is auditing and you are both guessing.

## When the trail is missing

A missing trail is not always a process failure. Sometimes it is a tooling failure — a webhook didn't fire, a comment didn't post, a link rotted when the tracker was archived. Either way, the cure is the same: backfill the link, add the missing comment, write the knowledge article, connect the audit row. The trail is what lets the team move fast without meetings. It is what lets a new person understand the decision without asking. It is what makes the postmortem short instead of long.

A missing trail is also not always an immediate problem. A one-line typo fix that touched no behavior does not need to point to five sources. But anything that affects policy, behavior, or constraints should have the trail visible and clickable. If you are ever unsure, assume it needs the trail. Backfill is cheap. Archaeology is expensive.

---

The evidence checklist is not paperwork. It is the system telling the future what the present did. It lets your team say "we decided" with confidence, not "something happened" with uncertainty. For more on why this matters, see [the book on auditable velocity](/book#what-you-are-actually-buying). For technical details on querying and trusting audit rows, see [the audit log chapter](/docs/operating/audit-log).
