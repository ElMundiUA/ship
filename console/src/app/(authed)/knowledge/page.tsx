import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import {
  type ApiActivatedRepo,
  type ApiBucket,
  ApiHttpError,
  listActivatedRepos,
  listBucketArticles,
  listIntegrations,
  listKnowledgeImportSources,
  listBuckets,
} from "@/lib/api/client";
import {
  getCachedMe,
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";
import type {
  ApiBucketArticle,
  ApiIntegration,
  ApiKnowledgeImportSource,
} from "@/lib/api/types";

import {
  KnowledgeControlCenter,
  type KnowledgeArticleRow,
  type KnowledgeBucketRow,
  type KnowledgeSourceRow,
} from "./knowledge-control-center";

export const dynamic = "force-dynamic";

type LiveData = {
  workspace: { id: string; slug: string; name: string };
  buckets: KnowledgeBucketRow[];
  articles: KnowledgeArticleRow[];
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
    const [repos, integrations, me, rawBuckets, importSources] =
      await Promise.all([
        listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
        listIntegrations(workspace.id, token).catch(() => [] as ApiIntegration[]),
        getCachedMe(),
        listBuckets(workspace.id, { token }),
        listKnowledgeImportSources(workspace.id, token).catch(
          () => [] as ApiKnowledgeImportSource[],
        ),
      ]);

    // Fetch the published-and-fresh article set for every bucket in
    // parallel. Six fixed buckets after the consolidation, so this is
    // a bounded fan-out — no pagination needed in the UI surface.
    const articlePairs = await Promise.all(
      rawBuckets.map(async (bucket) => ({
        bucket,
        articles: await listBucketArticles(
          workspace.id,
          bucket.slug,
          {},
          token,
        ).catch(() => [] as ApiBucketArticle[]),
      })),
    );

    const buckets: KnowledgeBucketRow[] = rawBuckets.map((bucket) =>
      toBucketRow(bucket),
    );
    const articles: KnowledgeArticleRow[] = articlePairs
      .flatMap(({ bucket, articles }) =>
        articles.map((article) => toArticleRow(article, bucket)),
      )
      .sort(
        (a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt),
      );
    const sources: KnowledgeSourceRow[] = importSources.map(toSourceRow);

    return {
      status: "live",
      data: {
        workspace: {
          id: workspace.id,
          slug: workspace.slug,
          name: workspace.name,
        },
        buckets,
        articles,
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

  const { workspace, buckets, articles, sources, repos, integrations, me } =
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
          buckets={buckets}
          articles={articles}
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


function toBucketRow(bucket: ApiBucket): KnowledgeBucketRow {
  return {
    id: bucket.id,
    slug: bucket.slug,
    name: bucket.name,
    description: bucket.description ?? "",
    archived: Boolean(bucket.archived_at),
  };
}


function toArticleRow(
  article: ApiBucketArticle,
  bucket: ApiBucket,
): KnowledgeArticleRow {
  return {
    id: article.id,
    bucketSlug: bucket.slug,
    bucketName: bucket.name,
    slug: article.slug,
    title: article.title || article.slug,
    snippet: firstParagraph(article.body_md),
    updatedAt: article.updated_at,
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


function firstParagraph(body: string): string {
  if (!body) return "";
  const text = body.trim();
  if (!text) return "";
  for (const chunk of text.split("\n\n")) {
    const candidate = chunk.replace(/^#+\s+/, "").trim();
    if (candidate) {
      return candidate.length > 220
        ? candidate.slice(0, 219).trimEnd() + "…"
        : candidate;
    }
  }
  return text.length > 220 ? text.slice(0, 219).trimEnd() + "…" : text;
}
