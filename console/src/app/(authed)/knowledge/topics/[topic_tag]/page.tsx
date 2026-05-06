/**
 * Knowledge — topic view detail.
 *
 * Reads ``GET /v1/workspaces/{ws}/knowledge/topic-views/{topic_tag}``
 * and renders the markdown body alongside the active claim list with
 * provenance.
 *
 * Layout (≥ lg):
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │  Title · topic_tag · claim_count · last_rendered_at       │
 *   ├──────────────────────────────────────┬───────────────────┤
 *   │  Rendered article body (markdown)    │  Claims list w/    │
 *   │  col-span-8                          │  source links      │
 *   │                                      │  col-span-4        │
 *   └──────────────────────────────────────┴───────────────────┘
 *
 * Below ``lg`` collapses to a single column, claims drop below the
 * body.
 */

import { redirect } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import {
  type ApiClaimSummary,
  type ApiTopicViewDetail,
  ApiHttpError,
  getTopicView,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import { relativeTime } from "@/lib/format";


export const dynamic = "force-dynamic";


type RouteParams = Promise<{ topic_tag: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;


type LoadResult =
  | { status: "live"; view: ApiTopicViewDetail; workspaceName: string }
  | { status: "missing" }
  | { status: "unavailable"; reason: string };


async function load(
  topicTag: string,
  searchParams: Record<string, string | string[] | undefined>,
): Promise<LoadResult> {
  const token = await getCachedSessionToken();
  if (!token) {
    redirect(
      `/login?next=%2Fknowledge%2Ftopics%2F${encodeURIComponent(topicTag)}&reason=session_expired`,
    );
  }

  const workspaceRows = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(searchParams, workspaceRows);
  const workspace = pickWorkspace(workspaceRows, resolved);

  try {
    const view = await getTopicView(workspace.id, topicTag, token);
    return { status: "live", view, workspaceName: workspace.name };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(
        `/login?next=%2Fknowledge%2Ftopics%2F${encodeURIComponent(topicTag)}&reason=session_expired`,
      );
    }
    if (err instanceof ApiHttpError && err.status === 404) {
      return { status: "missing" };
    }
    const message = err instanceof Error ? err.message : "Could not load topic.";
    return { status: "unavailable", reason: message };
  }
}


export default async function TopicViewPage({
  params,
  searchParams,
}: {
  params: RouteParams;
  searchParams?: SearchParams;
}) {
  const { topic_tag: topicTag } = await params;
  const sp = (await (searchParams ?? Promise.resolve({}))) ?? {};
  const result = await load(topicTag, sp);

  if (result.status === "unavailable") {
    return (
      <>
        <PageHeader kicker="knowledge" title={topicTag} />
        <PageBody>
          <ApiUnavailable scope="knowledge" details={result.reason} />
        </PageBody>
      </>
    );
  }

  if (result.status === "missing") {
    return (
      <>
        <PageHeader kicker="knowledge" title={topicTag} />
        <PageBody>
          <p className="text-sm text-white/55">
            No topic view rendered for this tag yet. Either fewer than 3
            active claims carry it, or the renderer cron hasn{"'"}t fired
            since this topic emerged.
          </p>
        </PageBody>
      </>
    );
  }

  const { view } = result;
  const isAuto =
    view.rendered_by_model && view.rendered_by_model !== "deterministic";

  return (
    <>
      <PageHeader kicker="knowledge / topic" title={view.title} />
      <PageBody>
        <div className="mx-auto max-w-6xl space-y-12">
          <header className="space-y-2">
            <p className="flex flex-wrap items-center gap-x-3 text-xs text-white/45">
              <span className="font-mono">{view.topic_tag}</span>
              <span className="text-white/20">·</span>
              <span>
                {view.claim_count} claim
                {view.claim_count === 1 ? "" : "s"}
              </span>
              <span className="text-white/20">·</span>
              <span>last rendered {relativeTime(view.last_rendered_at)}</span>
              {!isAuto && (
                <>
                  <span className="text-white/20">·</span>
                  <span className="text-coral/70">deterministic fallback</span>
                </>
              )}
            </p>
          </header>

          <div className="grid grid-cols-1 gap-x-12 gap-y-12 lg:grid-cols-12 2xl:gap-x-16">
            <article className="prose prose-invert max-w-none lg:col-span-8 2xl:col-span-9">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {view.body_md}
              </ReactMarkdown>
            </article>

            <aside className="space-y-4 lg:col-span-4 2xl:col-span-3">
              <h3 className="font-display text-xs font-bold uppercase tracking-widest text-white/55">
                Claims · {view.claims.length}
              </h3>
              <ul className="space-y-4">
                {view.claims.map((claim) => (
                  <li key={claim.id}>
                    <ClaimEntry claim={claim} />
                  </li>
                ))}
              </ul>
            </aside>
          </div>
        </div>
      </PageBody>
    </>
  );
}


function ClaimEntry({ claim }: { claim: ApiClaimSummary }) {
  const link = claim.source_links[0];
  return (
    <div className="space-y-1.5">
      <p className="text-sm leading-relaxed text-white/80">{claim.claim_md}</p>
      <p className="text-[11px] text-white/40">
        <span className="uppercase tracking-wider">{claim.kind}</span>
        {claim.topic_tags.length > 0 && (
          <>
            <span className="mx-2 text-white/20">·</span>
            <span className="font-mono">
              {claim.topic_tags.slice(0, 3).join(", ")}
              {claim.topic_tags.length > 3 && "…"}
            </span>
          </>
        )}
      </p>
      {link && (
        <p className="text-[11px] text-white/40">
          <span>source: </span>
          {link.external_url ? (
            <a
              href={link.external_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-aqua/80 hover:text-aqua"
            >
              {link.title || link.external_url}
            </a>
          ) : (
            <span>{link.title || "(no title)"}</span>
          )}
        </p>
      )}
    </div>
  );
}
