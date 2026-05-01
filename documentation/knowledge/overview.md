# What Knowledge Is For

The cruel joke of agentic tooling is how often we blame the wrong villain. We say the agent hallucinated, or the prompt was thin, or the temperature was high—as if the failure were a personality flaw. In practice, most wounds are specification wounds. An under-specified system fails the way a bridge fails when nobody agreed which load it must carry. And the reason specifications drift is simpler than we'd like to admit: when a team has no single place for "what is true here," the answer ends up in three places at once.

The rule lives in a Slack pin from last month. It lives in a paragraph someone pasted into a custom prompt. It lives in the head of whoever onboarded last, who did their best to infer it from watching a senior engineer work. All three versions are right, sort of, and all three drift independently. One gets forgotten. One gets half-remembered and cargo-culted into a related situation where it doesn't apply. One gets superseded by a decision that only made it into a half-read GitHub discussion. By the time you notice the mismatch, you've shipped different behavior in three systems, and nobody knows which one was right.

Knowledge solves this by being the single, reviewable place where a fact lives.

It is not a Slack channel or a private note or a prompt comment. It is a page in the Knowledge section of your workspace—written in plain prose, titled clearly, owned by a person—that agents and humans and reviewers can all lean on as the source of truth. When the rule changes, you don't re-teach three systems; you update one document and let the review process vouch that the change is intentional. When an agent needs to know something, it reads the same article a human would read if they were onboarding. When a reviewer wants to check whether an agent followed the rule, they read the same place the agent read.

## What Knowledge Holds

Some things belong in Knowledge. Others don't, and putting them there is a category error.

What lives here: code style conventions, test commands and how to read their output, brand rules and voice guidelines, how a third-party API works or which credentials to use, the current deploy procedure, which database tables are safe to migrate without downtime, the answer to a recurring clarification ("should we use your time or server time for dates?"), a runbook, a standing decision, the why behind a constraint. Anything that would go in a team RFC or a policy document, anything you'd want someone to cite when explaining why they did something, anything that will be true next month.

What doesn't: secrets or credentials, one-off tickets or completed incidents, raw chat transcripts that haven't been reviewed and synthesized, debate that's still in progress, provisional hypotheses, things that belong in code comments instead of standalone facts.

The boundary is not always sharp. A runbook that hasn't been tested yet lives in draft until someone has actually followed it. A decision that's being made in a pull request review should stay in the review thread until the decision is settled and worth codifying. An integration note that changes every time the vendor ships a release might belong in your repo's README until it stabilizes. The question to ask is: would I trust an agent to act on this as the final word, and would I be comfortable having it reviewed as if it were policy?

## Where Knowledge Lives

When you open Ship—at the `/knowledge` path in the console—you see your workspace's knowledge base. Each article lives in a bucket, which we cover in the next chapter. Articles can be scoped to the whole workspace, to a specific project, to a repository, or to a single user, depending on what you're sharing and with whom.

If your team writes in code instead of web forms, Knowledge mirrors from `.ship/knowledge/*.md` in any connected repository. A file you save in the repo follows the same review process as anything written in the console—an agent or a human can suggest changes, and those changes are reviewed before they go live. The system treats the console interface and the repository equally; both are windows into the same source.

You can also import knowledge from external sources—a Notion database, a docs repository, a wiki—if your team already has a place where standing rules live. The import process is covered in [Importing knowledge](./importing.md). However imported, all articles go through the same review gate before agents use them.

## Why Short Articles Age Better Than Handbooks

A team handbook is a museum. It's written once, with great care, and then treated with the reverence of a founding document. When the rule changes—and it will—people are afraid to touch the section because they might break something. They add a footnote. Someone else adds a caveat to the footnote. By the time you need the rule, you're parsing a palimpsest, and you're not sure which layer is current.

An article is a unit of truth. It is small enough to own cleanly and specific enough that when the rule changes, you don't revise the handbook—you supersede or archive the old article and write a new one. The history is visible, the ownership is clear, and there's no ambiguity about which version you should follow.

When you author knowledge, assume you're writing something that will need to change. A clear title, one topic, the current rule as it is today, examples only where they reduce ambiguity rather than fill space—these habits make change easy. Articles that pack in twenty implicit assumptions and three unrelated use cases are articles nobody will touch when they need updating.

A good article includes its provenance: who wrote it, when, and why—or at minimum, who owns it now and can answer questions. Five lines of source and ownership beat fifty lines of guessable code style, because when you're not sure, you can ask the owner instead of guessing. When the owner changes jobs, the next owner can find the context they need to make the right call.

The simplest articles are often the best ones. A title. A one-sentence rule. A link to the source decision or the person who can explain the why. Done.

## Scopes and the Shape of Knowledge

Knowledge is divided into buckets—logical groupings that help you find what you're looking for without having to read everything. The next chapter explains the unit of division and how to think about splitting knowledge into buckets that stay useful as your team grows.
