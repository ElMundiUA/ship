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
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import {
  type ApiClaimSummary,
  type ApiTopicViewDetail,
  ApiHttpError,
  getTopicView,
  listActivatedRepos,
} from "@/lib/api/client";
import {
  getCachedMe,
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import { relativeTime } from "@/lib/format";


export const dynamic = "force-dynamic";


type RouteParams = Promise<{ topic_tag: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;


type ScopeContext = {
  workspaceName: string;
  repos: { id: string; full_name: string }[];
  me: {
    id: string;
    email: string;
    display_name: string | null;
  } | null;
};

type LoadResult =
  | { status: "live"; view: ApiTopicViewDetail; scope: ScopeContext }
  | { status: "missing"; scope: ScopeContext }
  | { status: "unavailable"; reason: string; scope: ScopeContext };

function buildScopePill(scope: ScopeContext) {
  return (
    <ScopePill
      workspaceName={scope.workspaceName}
      repos={scope.repos}
      me={scope.me}
    />
  );
}

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

  const [repos, me] = await Promise.all([
    listActivatedRepos(workspace.id, token).catch(() => []),
    getCachedMe(),
  ]);
  const scope: ScopeContext = {
    workspaceName: workspace.name,
    repos: repos.map((repo) => ({
      id: repo.id,
      full_name: repo.full_name,
    })),
    me: me
      ? {
          id: me.id,
          email: me.email,
          display_name: me.display_name,
        }
      : null,
  };

  try {
    const view = await getTopicView(workspace.id, topicTag, token);
    return { status: "live", view, scope };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(
        `/login?next=%2Fknowledge%2Ftopics%2F${encodeURIComponent(topicTag)}&reason=session_expired`,
      );
    }
    if (err instanceof ApiHttpError && err.status === 404) {
      return { status: "missing", scope };
    }
    const message = err instanceof Error ? err.message : "Could not load topic.";
    return { status: "unavailable", reason: message, scope };
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

  const scopePill = buildScopePill(result.scope);

  if (result.status === "unavailable") {
    return (
      <>
        <PageHeader kicker="knowledge" title={topicTag} scopePill={scopePill} />
        <PageBody>
          <div data-testid="topic-view-unavailable">
            <ApiUnavailable scope="knowledge" details={result.reason} />
          </div>
        </PageBody>
      </>
    );
  }

  if (result.status === "missing") {
    return (
      <>
        <PageHeader kicker="knowledge" title={topicTag} scopePill={scopePill} />
        <PageBody>
          <p
            data-testid="topic-view-empty"
            className="text-sm text-white/55"
          >
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
      <PageHeader
        kicker="knowledge / topic"
        title={view.title}
        scopePill={scopePill}
      />
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
            <article
              data-testid="topic-view-article"
              className="max-w-3xl lg:col-span-8 2xl:col-span-9"
            >
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={MARKDOWN_COMPONENTS}
              >
                {stripLeadingH1(view.body_md)}
              </ReactMarkdown>
            </article>

            <aside
              data-testid="topic-view-claims"
              className="space-y-4 lg:col-span-4 2xl:col-span-3"
            >
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


// ---------------------------------------------------------------------------
// Markdown rendering — explicit component map.
//
// Tailwind Typography (the ``prose`` class) isn't installed in the
// console build, so the default ReactMarkdown output rendered every
// element with browser-default styling and the body looked like an
// undifferentiated wall. We map each tag to a styled element instead;
// keeps the bundle slim and matches the editorial dark theme exactly.
// ---------------------------------------------------------------------------


function stripLeadingH1(body: string): string {
  // Renderer prompt instructs the LLM to lead with ``# <Title>``;
  // the page header already shows that title, so duplicating it
  // inside the body adds visual noise. Strip the first H1 + any
  // blank lines that immediately follow it. Subsequent headings
  // (``# Section``) — rare but possible — survive untouched.
  const lines = (body || "").split("\n");
  let cursor = 0;
  while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  if (cursor < lines.length && /^#\s+/.test(lines[cursor])) {
    cursor++;
    while (cursor < lines.length && lines[cursor].trim() === "") cursor++;
  }
  return lines.slice(cursor).join("\n");
}


const MARKDOWN_COMPONENTS: Components = {
  h1: ({ children }) => (
    <h1 className="mt-10 mb-4 font-display text-2xl font-bold leading-tight text-white first:mt-0 md:text-3xl">
      {children}
    </h1>
  ),
  h2: ({ children }) => (
    <h2 className="mt-10 mb-3 font-display text-xl font-bold leading-tight text-white first:mt-0">
      {children}
    </h2>
  ),
  h3: ({ children }) => (
    <h3 className="mt-8 mb-2 font-display text-base font-semibold uppercase tracking-wider text-white/85 first:mt-0">
      {children}
    </h3>
  ),
  h4: ({ children }) => (
    <h4 className="mt-6 mb-2 text-sm font-semibold uppercase tracking-wider text-white/70 first:mt-0">
      {children}
    </h4>
  ),
  p: ({ children }) => (
    <p className="my-4 text-base leading-relaxed text-white/75">{children}</p>
  ),
  ul: ({ children }) => (
    <ul className="my-4 list-disc space-y-2 pl-6 text-base leading-relaxed text-white/75 marker:text-white/30">
      {children}
    </ul>
  ),
  ol: ({ children }) => (
    <ol className="my-4 list-decimal space-y-2 pl-6 text-base leading-relaxed text-white/75 marker:text-white/30">
      {children}
    </ol>
  ),
  li: ({ children }) => <li className="pl-1">{children}</li>,
  a: ({ children, href }) => (
    <a
      href={href}
      className="text-aqua underline-offset-4 transition hover:underline"
      target={href?.startsWith("http") ? "_blank" : undefined}
      rel={href?.startsWith("http") ? "noopener noreferrer" : undefined}
    >
      {children}
    </a>
  ),
  code: ({ children, className }) => {
    // ReactMarkdown gives ``code`` both for inline ``code`` and for
    // fenced blocks (the parent wraps in ``pre``). The className
    // ``language-foo`` is only attached to fenced ones, so we route
    // on its presence.
    if (className) {
      return (
        <code className={`${className} block`}>{children}</code>
      );
    }
    return (
      <code className="rounded bg-white/[0.07] px-1.5 py-0.5 font-mono text-[0.92em] text-mist">
        {children}
      </code>
    );
  },
  pre: ({ children }) => (
    <pre className="my-5 overflow-x-auto rounded-md bg-white/[0.04] p-4 font-mono text-sm leading-relaxed text-mist">
      {children}
    </pre>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-5 border-l-2 border-aqua/40 pl-4 text-base italic leading-relaxed text-white/65">
      {children}
    </blockquote>
  ),
  hr: () => <hr className="my-8 border-white/10" />,
  strong: ({ children }) => (
    <strong className="font-semibold text-white">{children}</strong>
  ),
  em: ({ children }) => (
    <em className="italic text-white/80">{children}</em>
  ),
  table: ({ children }) => (
    <div className="my-5 overflow-x-auto">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }) => (
    <thead className="border-b border-white/15 text-left text-xs uppercase tracking-wider text-white/55">
      {children}
    </thead>
  ),
  th: ({ children }) => <th className="px-3 py-2 font-semibold">{children}</th>,
  td: ({ children }) => (
    <td className="border-b border-white/5 px-3 py-2 align-top text-white/75">
      {children}
    </td>
  ),
};


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
