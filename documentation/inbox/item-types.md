# The five item types

A small taxonomy is a feature. Five kinds is the smallest set that covers what we actually see in production without forcing everything into one bin. Don't add a sixth lightly — it dilutes the contract. When you open the Inbox, you need to know at a glance what you're looking at and what you have to do. Each of the five kinds has a single canonical action. That clarity — knowing the shape of each item before you read past the headline — is what makes the Inbox legible instead of overwhelming.

## Clarification

The agent or routine paused because it lacked a fact it needed before acting. These are gaps in knowledge: a question the routine cannot answer from what it has seen so far. Example: "PR #1234 mentions 'the new tier'; which tier is that?" or "this DNS entry could belong to two customers; which one owns it?" The canonical action is **answer** — you provide the missing fact, the routine resumes and continues. If the same clarification keeps coming back, the right fix is usually a knowledge article, not a faster typing speed. The knowledge base exists so routines don't have to keep asking.

## Improvement

Something is working, but a better path was spotted. These are suggestions: the routine completed its task successfully, but inspection found a way to do it faster, cheaper, or more reliably. Example: "this routine could skip the diff if no relevant files changed" or "we're now using GraphQL for this query; this REST call is redundant." The canonical action is **accept** — you opt in, the change is applied to the routine config or standing rule. You can also **decline** or **defer**; both are real answers, not failures. An accepted improvement stays; a declined one closes the suggestion and returns to the current path. Defer is for "this is right, but the timing is wrong."

## Approval

A decision needs explicit human consent before something irreversible happens. These are gates: the routine has a clear path forward and is waiting at the boundary where human judgment must sign off. Example: "merge this PR into `main`?" or "deactivate this connected repo?" or "archive these 150 stale tickets?" The canonical actions are **approve** or **reject** — both are recorded with the rationale you give. Approvals are not rubber stamps; if the decision feels wrong, rejecting is the right answer. Rejection includes a reason so the routine and its next human reader both know why this step did not proceed.

## Failure

A configured action failed and is sitting waiting for a person. These are breakdowns: the routine tried and hit an error that stopped it cold. Example: "routine `daily_security_review` returned an error from the LLM provider three times in a row" or "the credential for this Slack workspace expired and we cannot refresh it." The canonical action is **retry** — restart the routine with the same inputs. But don't retry blindly; check whether the failure is transient (provider rate limit, network hiccup) or structural (missing credential, wrong scope, deprecated API). A transient failure deserves a retry; a structural one deserves a fix first.

## Exception

The normal rule needs a named override. These are acknowledgements: the routine would normally do one thing, but a specific circumstance calls for a different behavior, and you are putting that override on record. Example: "the SLA contract for this customer says we don't auto-archive their tickets" or "this repository uses a non-standard branch naming convention; accept it." The canonical action is **acknowledge** — record that you have seen and accepted the override. The audit log is what makes acknowledgements honest; an unacknowledged exception silently drifts into folklore. When you acknowledge an exception, the system records when you did it, what you said, and why the routine should behave differently this one time.

## What does not belong

Pure status events — "routine ran, found nothing", "sync completed" — belong in the audit log, not the Inbox. Informational logs and debug noise belong on the originating ticket or in a detail view, not here. The temptation to use the Inbox as a fire-hose is the temptation to make the Inbox useless. Keep the Inbox for items that require a *decision*. See [disposition](/docs/inbox/disposition) for the action vocabulary and how to apply each one.
