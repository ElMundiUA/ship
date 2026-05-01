# The distiller and the review path

Nothing becomes knowledge silently. When you import documents, route inbox notes, or mirror configuration files, the system must avoid the failure mode of promotion—turning half-formed sources into policy without human eyes in the loop. The distiller is the service that prevents this. It ingests raw text from many origins, proposes a topic and scope, drafts an article, and then stops. A human reviewer opens the draft, checks it against the source, and decides whether to publish it for agents and citation, to supersede an existing article with a newer version, to archive it when the fact no longer holds, or to send it back for more context. This path is the safety boundary between "we collected text" and "agents now act on it."

## Raw input

Every piece of knowledge starts as raw input: an imported webpage or PDF, a Notion or Confluence document mirrored from a workspace, a file fetched from a docs repository, an uploaded file from a team member, or a `.ship/knowledge` entry checked into your project repository. Inbox notes flagged by a routine as candidates for knowledge—clarifications or improvements that should be turned into articles rather than answered and forgotten—also flow through this path. Each raw document carries full provenance: the source kind (import, mirror, repo file, upload, routed note), the source URL or file path, the fetch timestamp, and the route that brought it in. This metadata lives on the raw input and persists through every phase that follows, so you can always trace an article back to where it came from.

## Routing

The distiller's first job is to answer: where does this belong? The system computes a vector centroid from your existing knowledge buckets and reads any human hint that came with the source—a note saying "this goes in the config bucket" or "this is user-scoped." An LLM-backed router then nominates the right bucket, or flags that no existing bucket is a good fit. If the router is unsure, the draft waits in the queue with the routing decision marked as pending, and no bucket is assigned until a human chooses. Nothing is invented. The four bucket scopes—workspace, project, repo, and user—match the resolver's fetch scope, so routing also locks in which agents will eventually see the article.

## Synthesis

Once the bucket is chosen, the distiller drafts the actual article: a concise title, the current rule or fact stated plainly, and examples only where they reduce ambiguity rather than expand it. The draft includes a provenance footer that links back to the source and notes when it was imported. The synthesis is deliberately conservative—if the distiller cannot be sure of something, it leaves the section blank and asks the human reviewer to fill it in rather than guessing or paraphrasing beyond what the source supports. Drafts are private to reviewers and do not appear in the resolver until they are published; you can review them at your own pace without the risk that an agent will act on incomplete work.

## Review

A human reviewer opens the draft article in the review queue, reads it against the original source, and edits or rewrites where needed to match your team's voice and level of detail. The queue is filterable by bucket and by scope, so you can focus on workspace-level policy one day and repo-specific conventions another. Once you are satisfied with the draft, you choose one of four actions: publish it to make it available to agents and citations on the next resolver cycle; supersede an existing article, which keeps the old version readable and links forward to the new one for a clean record of what changed and when; archive the article if something stops being true rather than being replaced, removing it from resolver scope without deleting the historical record; or send it back to the distiller for more context, which keeps the draft private and marks it as needing clarification or additional sources. Reviewers can also escalate—turn the draft into an Inbox approval if the article touches policy and should be confirmed by a wider group before publication.

## Publish, supersede, archive

A published article becomes available to agents and citations on the next resolver cycle. When you supersede an existing article with a newer version, both remain visible in the knowledge interface, linked forward and backward so the team can see what changed, when it changed, and why. This keeps a clean audit trail and prevents the confusion of not knowing which version was active when. Archiving moves an article out of resolver scope and removes it from agent searches, but the article and its history stay readable—useful when something stops being true (a vendor changes their API, a tool goes out of use) rather than being replaced by a newer fact. The archive serves as a reference for why decisions were made the way they were.

## Cadence and queue health

Most teams discover that their distiller queue is healthiest when reviewers spend ten to fifteen minutes a day on it, not an hour once a week. A growing backlog often signals one of three things: imports are too aggressive and bringing in noise that does not belong, existing buckets need a split because the coverage area has become too broad, or the team has been routing inbox notes that should have been answered and forgotten instead. The review queue is honest about team discipline in a way a dashboard is not. If the queue is full, something in your ingestion or routing is out of tune. If the queue is empty, you are probably not importing enough, or your team is answering many questions that should be documented. The queue becomes a diagnostic tool the more you use it.

---

For the next step in your knowledge workflow, read [Chat as a knowledge tool](/docs/knowledge/chat-as-knowledge).
