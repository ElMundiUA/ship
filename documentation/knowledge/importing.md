# Importing knowledge

Knowledge can come from anywhere your team already works. You can author standing rules directly in the console for handcrafted policies and patterns, or you can import content from an external source. Importing brings raw documents into your workspace where they flow through the distiller for review — imports never publish silently. This chapter walks you through each path: mirrored `.ship/knowledge` files in your repos, websites, SaaS platforms like Notion and Confluence, standalone docs repositories, and one-off file uploads.

## .ship/knowledge in a connected repo

Markdown files under `.ship/knowledge/` in any activated repository automatically mirror into your workspace. This is where code style guides, test commands, runbooks, and other pieces that live next to the code belong. Because these files go through normal pull request review as part of the repo workflow, there's no separate review step for the knowledge ingestion — your code reviewers see the changes. Edits sync on the next repository pull. This is the path most engineering teams prefer, since it keeps agent context close to the source and under version control.

## Website

Point the importer at a URL and Firecrawl crawls and scrapes the content into Markdown. This works well for vendor documentation, public API references, or a publicly accessible knowledge base your team relies on regularly. Scope the crawl to a URL path prefix when you can — full-domain crawls of large sites produce noise and rarely justify the storage. No authentication is typically needed for public sites, though some Firecrawl plans support authenticated crawls if you're accessing gated content.

## Notion

The importer reads connected Notion pages and databases as a knowledge source. You'll need the Notion integration wired up — see [Integrations beyond the tracker](../../setup/integrations) — and the specific pages or databases shared with the integration. This path suits design docs, decision logs, or company policies that are already written and maintained in Notion and don't have an obvious home in the repository.

## Confluence

Same shape as Notion: the importer reads connected pages from a Confluence space. Requires the Confluence integration to be configured. This is the right answer for teams whose institutional knowledge already lives in Confluence — typically larger organizations with mature documentation practices and centralized knowledge management.

## Docs repo

Read Markdown documentation from an activated repository, usually a dedicated `docs/` folder rather than `.ship/knowledge`. This is useful when a team maintains a standalone documentation site — for example, a public product docs site — and wants the same content available to agents without duplicating it. Unlike `.ship/knowledge`, which is "agent-facing context kept next to the code," a docs repo is "the documentation site itself."

## Uploaded files

A one-shot import for files without periodic sync. Drag a Markdown or plain text file into the console and it lands as a draft article in the bucket of your choice. Use this path to migrate content from one-off sources — a Slack thread someone wrote, an email with useful context, a PDF you've already converted to Markdown — without the overhead of setting up an integration just to get one document in.

---

Every import path above produces *raw documents*, not published articles. The distiller proposes topics, scope, and structure; a human reviews the work before publishing. The next chapter — [The distiller and review](/docs/knowledge/distiller-and-review) — walks the review process and shows you how to refine raw documents into polished articles.
