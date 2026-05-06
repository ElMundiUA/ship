import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import {
  type ApiActivatedRepo,
  ApiHttpError,
  listActivatedRepos,
  listIntegrations,
  listKnowledgeImportSources,
  listTopicViews,
} from "@/lib/api/client";
import {
  getCachedMe,
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import type {
  ApiIntegration,
  ApiKnowledgeImportSource,
} from "@/lib/api/types";

import {
  KnowledgeControlCenter,
  type KnowledgeSourceRow,
  type KnowledgeTopicViewRow,
} from "./knowledge-control-center";

export const dynamic = "force-dynamic";

type LiveData = {
  workspace: { id: string; slug: string; name: string };
  topicViews: KnowledgeTopicViewRow[];
  sources: KnowledgeSourceRow[];
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
  me: Awaited<ReturnType<typeof getCachedMe>>;
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

type LoadResult =
  | { status: "live"; data: LiveData }
  | { status: "unavailable"; reason: string };


async function load(
  params: Record<string, string | string[] | undefined>,
): Promise<LoadResult> {
  const token = await getCachedSessionToken();
  if (!token) {
    redirect("/login?next=%2Fknowledge&reason=session_expired");
  }

  const workspaceRows = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(params, workspaceRows);
  const workspace = pickWorkspace(workspaceRows, resolved);

  try {
    // Knowledge index reads the claim-graph canon directly. Legacy
    // ``bucket_articles`` are intentionally NOT fetched here: keeping
    // them on the page masks pipeline failures (operator sees stale
    // articles from days ago and assumes the system is fine while
    // the new extractor / reconciler / renderer chain has been
    // silently broken). If the canon is empty, the page should look
    // empty — that's the signal that something upstream needs
    // attention.
    const [repos, integrations, me, rawTopicViews, importSources] =
      await Promise.all([
        listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
        listIntegrations(workspace.id, token).catch(() => [] as ApiIntegration[]),
        getCachedMe(),
        listTopicViews(workspace.id, { limit: 200 }, token),
        listKnowledgeImportSources(workspace.id, token).catch(
          () => [] as ApiKnowledgeImportSource[],
        ),
      ]);

    const topicViews: KnowledgeTopicViewRow[] = rawTopicViews.map(toTopicViewRow);
    const sources: KnowledgeSourceRow[] = importSources.map(toSourceRow);

    return {
      status: "live",
      data: {
        workspace: {
          id: workspace.id,
          slug: workspace.slug,
          name: workspace.name,
        },
        topicViews,
        sources,
        repos,
        integrations,
        me,
      },
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fknowledge&reason=session_expired");
    }
    const message = err instanceof Error ? err.message : "Could not load knowledge.";
    return { status: "unavailable", reason: message };
  }
}


export default async function KnowledgeIndexPage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) ?? {};
  const result = await load(params);

  if (result.status === "unavailable") {
    return (
      <>
        <PageHeader kicker="knowledge" title="Knowledge" />
        <PageBody>
          <ApiUnavailable scope="knowledge" details={result.reason} />
        </PageBody>
      </>
    );
  }

  const { workspace, topicViews, sources, repos, integrations, me } =
    result.data;

  const scopePill = (
    <ScopePill
      workspaceName={workspace.name}
      repos={repos.map((repo) => ({
        id: repo.id,
        full_name: repo.full_name,
      }))}
      me={
        me
          ? {
              id: me.id,
              email: me.email,
              display_name: me.display_name,
            }
          : null
      }
    />
  );

  return (
    <>
      <PageHeader title="Knowledge" scopePill={scopePill} />
      <PageBody>
        <KnowledgeControlCenter
          workspace={workspace}
          topicViews={topicViews}
          sources={sources}
          repos={repos}
          integrations={integrations}
        />
      </PageBody>
    </>
  );
}


// ---------------------------------------------------------------------------
// Mappers
// ---------------------------------------------------------------------------


function toTopicViewRow(
  view: import("@/lib/api/client").ApiTopicViewSummary,
): KnowledgeTopicViewRow {
  return {
    topicTag: view.topic_tag,
    title: view.title || view.topic_tag,
    snippet: view.snippet,
    claimCount: view.claim_count,
    renderedByModel: view.rendered_by_model,
    lastRenderedAt: view.last_rendered_at,
  };
}


function toSourceRow(source: ApiKnowledgeImportSource): KnowledgeSourceRow {
  return {
    id: source.id,
    name: source.name,
    kind: source.kind,
    status: source.status,
    lastSyncedAt: source.last_synced_at,
    lastError: source.last_error,
  };
}


