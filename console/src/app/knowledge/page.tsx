import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ApiUnavailable } from "@/components/api-unavailable";
import { ScopePill } from "@/components/scope-pill";
import {
  type ApiActivatedRepo,
  type ApiBucket,
  type ApiKnowledgeCanonicalResponse,
  ApiHttpError,
  getKnowledgeCanonical,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listBucketSources,
  listKnowledgeImportSources,
  listBuckets,
  listIntegrations,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  toAppShellWorkspaces,
} from "@/lib/workspace-scope";
import type {
  ApiIntegration,
  ApiKnowledgeImportSource,
  ApiKnowledgeSource,
  ApiUser,
} from "@/lib/api/types";

import {
  KnowledgeControlCenter,
  type KnowledgeControlBucket,
  type KnowledgeControlSource,
  type KnowledgeBucketStatus,
} from "./knowledge-control-center";

export const dynamic = "force-dynamic";

type LiveData = {
  workspace: { id: string; slug: string; name: string };
  allWorkspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  buckets: KnowledgeControlBucket[];
  sources: KnowledgeControlSource[];
  canonical: ApiKnowledgeCanonicalResponse | null;
  repos: ApiActivatedRepo[];
  integrations: ApiIntegration[];
  me: ApiUser | null;
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

type LoadResult =
  | { status: "live"; data: LiveData }
  | { status: "unavailable"; reason: string };

async function load(
  params: Record<string, string | string[] | undefined>,
): Promise<LoadResult> {
  if (!isApiConfigured()) {
    return {
      status: "unavailable",
      reason: "SHIP_API_URL is not set on this deployment."
    };
  }

  const token = await getSessionToken();
  if (!token) {
    redirect("/login?next=%2Fknowledge&reason=session_expired");
  }

  let workspaceRows: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaceRows = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fknowledge&reason=session_expired");
    }
    return {
      status: "unavailable",
      reason: err instanceof Error ? err.message : "Could not load workspaces.",
    };
  }

  if (workspaceRows.length === 0) {
    redirect("/onboarding?step=github");
  }

  const resolved = await getResolvedWorkspaceId(params, workspaceRows);
  const workspace = pickWorkspace(workspaceRows, resolved);

  try {
    const [repos, integrations, me, rawBuckets, canonical, importSources] =
      await Promise.all([
        listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
        listIntegrations(workspace.id, token).catch(() => [] as ApiIntegration[]),
        getMe(token).catch(() => null as ApiUser | null),
        listBuckets(workspace.id, { token }),
        getKnowledgeCanonical(workspace.id, token).catch(
          () => null as ApiKnowledgeCanonicalResponse | null,
        ),
        listKnowledgeImportSources(workspace.id, token).catch(
          () => [] as ApiKnowledgeImportSource[],
        ),
      ]);

    const sourcePairs = await Promise.all(
      rawBuckets.map(async (bucket) => ({
        bucket,
        sources: await listBucketSources(workspace.id, bucket.slug, token).catch(
          () => [] as ApiKnowledgeSource[],
        ),
      })),
    );
    const sources = [
      ...importSources.map(normalizeImportSource),
      ...sourcePairs.flatMap(({ bucket, sources }) =>
        sources.map((source) => normalizeSource(bucket, source)),
      ),
    ];

    return {
      status: "live",
      data: {
        workspace: { id: workspace.id, slug: workspace.slug, name: workspace.name },
        allWorkspaces: workspaceRows,
        buckets: sourcePairs.map(({ bucket, sources }) =>
          normalizeBucket(bucket, sources),
        ),
        sources,
        canonical,
        repos,
        integrations,
        me,
      },
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fknowledge&reason=session_expired");
    }
    const message = err instanceof Error ? err.message : "Could not load knowledge buckets.";
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
      <AppShell kicker="knowledge" title="Knowledge">
        <ApiUnavailable scope="knowledge" details={result.reason} />
      </AppShell>
    );
  }

  const { workspace, allWorkspaces, buckets, sources, canonical, repos, integrations, me } = result.data;

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
    <AppShell
      kicker={`${workspace.name} · knowledge`}
      title="Knowledge"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      allWorkspaces={toAppShellWorkspaces(allWorkspaces)}
      scopePill={scopePill}
    >
      <KnowledgeControlCenter
        mode="live"
        workspace={workspace}
        buckets={buckets}
        sources={sources}
        canonical={canonical}
        repos={repos}
        integrations={integrations}
        defaultScope="workspace"
      />
    </AppShell>
  );
}

function normalizeBucket(
  bucket: ApiBucket,
  sources: ApiKnowledgeSource[],
): KnowledgeControlBucket {
  const metadata = metadataFromRef(bucket.source_ref);
  const lastIndexedAt =
    maxDate(sources.map((source) => source.last_synced_at)) ?? bucket.updated_at;
  const sourceCount =
    sources.length > 0 ? sources.length : bucket.source_kind ? 1 : 0;

  return {
    id: bucket.id,
    slug: bucket.slug,
    name: bucket.name,
    description: bucket.description ?? "",
    bucketType: metadata.bucketType ?? inferBucketType(bucket),
    scope: bucket.scope_kind ?? "workspace",
    sourceKind: bucket.source_kind ?? "agent_memory",
    authority: metadata.authority ?? inferAuthority(bucket),
    accessLevel: metadata.accessLevel ?? inferAccess(bucket),
    freshnessPolicy: metadata.freshnessPolicy ?? "Manual refresh",
    status: bucketStatus(bucket, sources),
    articles: bucket.summary_count ?? 0,
    chunks: inferChunks(bucket, sources),
    sourceCount,
    sourceNames:
      sources.length > 0
        ? sources.map((source) => source.kind)
        : bucket.source_kind
          ? [bucket.source_kind]
          : [],
    lastIndexedAt,
    updatedAt: bucket.updated_at,
  };
}

function normalizeSource(
  bucket: ApiBucket,
  source: ApiKnowledgeSource,
): KnowledgeControlSource {
  return {
    id: source.id,
    bucketSlug: bucket.slug,
    bucketName: bucket.name,
    kind: source.kind,
    status: source.status,
    lastSyncedAt: source.last_synced_at,
    nextSyncAt: asString(source.config.next_sync_at),
    lastError: source.last_error,
    documents: asNumber(source.config.document_count),
    chunks: asNumber(source.config.chunk_count),
    urlOrPath:
      asString(source.config.url) ??
      asString(source.config.path) ??
      asString(source.config.page_id) ??
      asString(source.config.space_key),
  };
}

function normalizeImportSource(
  source: ApiKnowledgeImportSource,
): KnowledgeControlSource {
  const config = source.config ?? {};
  return {
    id: source.id,
    bucketSlug: null,
    bucketName: "Auto-routed",
    kind: source.kind,
    status: source.status,
    lastSyncedAt: source.last_synced_at,
    nextSyncAt: null,
    lastError: source.last_error,
    documents: asNumber(config.document_count),
    chunks: null,
    urlOrPath:
      asString(config.url) ??
      asString(config.root_url) ??
      asString(config.path) ??
      source.name,
  };
}

function bucketStatus(
  bucket: ApiBucket,
  sources: ApiKnowledgeSource[],
): KnowledgeBucketStatus {
  if (bucket.archived_at) return "Stale";
  if (sources.some((source) => source.status === "error")) return "Failed";
  if (sources.some((source) => source.status === "syncing")) return "Indexing";
  if ((bucket.summary_count ?? 0) === 0 && sources.length === 0) return "Empty";
  const lastIndexedAt = maxDate(sources.map((source) => source.last_synced_at));
  if (lastIndexedAt && Date.now() - Date.parse(lastIndexedAt) > 30 * 86_400_000) {
    return "Stale";
  }
  return "Ready";
}

function metadataFromRef(sourceRef: Record<string, unknown> | null | undefined) {
  const raw =
    sourceRef &&
    typeof sourceRef.knowledge_metadata === "object" &&
    sourceRef.knowledge_metadata !== null
      ? (sourceRef.knowledge_metadata as Record<string, unknown>)
      : {};
  return {
    bucketType: asString(raw.bucket_type),
    authority: asString(raw.authority),
    accessLevel: asString(raw.access_level),
    freshnessPolicy: asString(raw.freshness_policy),
  };
}

function inferBucketType(bucket: ApiBucket): string {
  const haystack = `${bucket.name} ${bucket.slug} ${bucket.description ?? ""}`.toLowerCase();
  if (haystack.includes("architect")) return "Architecture";
  if (haystack.includes("runbook") || haystack.includes("ops")) return "Runbooks";
  if (haystack.includes("security") || haystack.includes("access")) return "Security";
  if (haystack.includes("product")) return "Product Knowledge";
  if (haystack.includes("glossary") || haystack.includes("data")) return "Data Glossary";
  if (bucket.source_kind === "repo_context" || bucket.source_kind === "repo_files") {
    return "Source Intelligence";
  }
  return "Custom";
}

function inferAuthority(bucket: ApiBucket): string {
  if (bucket.scope_kind === "workspace" && bucket.source_kind === "promoted") {
    return "Source of truth";
  }
  if (bucket.source_kind === "agent_memory" || bucket.source_kind === "repo_context") {
    return "Generated / low-authority";
  }
  if (bucket.source_kind === "audio_transcript") return "Temporary memory";
  return "High-confidence reference";
}

function inferAccess(bucket: ApiBucket): string {
  if (bucket.scope_kind === "user") return "Restricted";
  if (bucket.scope_kind === "repo") return "Repository-specific";
  return "Workspace";
}

function inferChunks(bucket: ApiBucket, sources: ApiKnowledgeSource[]): number {
  const sourceChunks = sources.reduce(
    (sum, source) => sum + (asNumber(source.config.chunk_count) ?? 0),
    0,
  );
  if (sourceChunks > 0) return sourceChunks;
  return (bucket.summary_count ?? 0) * 8;
}

function maxDate(values: Array<string | null | undefined>): string | null {
  const times = values
    .map((value) => (value ? Date.parse(value) : Number.NaN))
    .filter((value) => Number.isFinite(value));
  if (times.length === 0) return null;
  return new Date(Math.max(...times)).toISOString();
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
