# Knowledge

Knowledge is the product and repo context Ship can use without guessing. It keeps prompts thin and decisions traceable: code style, test commands, product rules, runbooks, brand voice, integration notes, and lessons from repeated clarifications.

Patterns and automations describe **how** work should happen. Knowledge describes **what is true here**.

## What belongs in knowledge

Add knowledge when the answer is reusable:

- how to run tests in this repo;
- how the product names features or customer segments;
- which API contracts are stable;
- which rollout or rollback policy applies;
- which files are safe or unsafe to touch;
- how a recurring clarification should be answered next time.

Do not use knowledge for secrets, temporary chat transcripts without review, or one-off decisions that belong on a ticket.

## Scope

Knowledge can live at different scopes:

| Scope | Use it for |
| --- | --- |
| Workspace | Company or team-wide policy, brand, security, delivery rules. |
| Project | Shared facts across related repos or a product line. |
| Repo | Code style, test commands, runbooks, architecture notes. |
| User | Private memory for the signed-in user where supported. |

The resolver prefers the most specific relevant knowledge. Repo knowledge can override project or workspace guidance when that repo has a real exception.

## Repo knowledge

The simplest source is Markdown under `.ship/knowledge/*.md` in a connected repo. Keep files short and named by topic:

```text
.ship/knowledge/
  code-style.md
  testing.md
  product-context.md
  release.md
```

When the repo is activated or updated, Ship mirrors these files into the workspace knowledge surface. Edits should go through normal review because agents may use the content later.

## Imported knowledge

The console can also ingest or sync knowledge from uploaded content and connected sources such as Notion or tracker items where the backend supports it. Imported content should still have provenance: where it came from, when it was fetched, and who approved it for use.

## Distillation and review

Ship can classify raw content into knowledge articles, but human review still matters. The safe flow is:

1. Import or mirror content.
2. Let the system propose topic and scope.
3. Review the article.
4. Publish it for agents and reviewers.
5. Supersede or archive it when it changes.

No knowledge path should silently turn a private note into policy.

## Knowledge and the Inbox

Knowledge is reference material. Inbox items are decisions.

If the same clarification appears repeatedly, add or update knowledge. If a piece of knowledge creates a new risk or trade-off, open or route an Inbox item so a human can decide.

## Good article shape

A useful article usually has:

- a clear title;
- one topic;
- the current rule or fact;
- examples only where they reduce ambiguity;
- source or provenance;
- owner or review expectation.

Short articles age better than broad handbooks.

## Technical reference

Engineers may see this implemented as buckets, articles, source kinds, distiller runs, and resolver scopes under the `/v1` API. Those names are implementation detail. Product docs should say knowledge unless they are explaining backend or CLI behavior.

## Where to next

Read [Concepts](./concepts.md) for the product vocabulary, [Operating](./operating.md) for day-to-day use, [Configuration](./configuration.md) for `.ship/knowledge`, and [Authoring](./authoring.md) when deciding whether a rule belongs in a reusable artifact or a workspace-specific knowledge article.
