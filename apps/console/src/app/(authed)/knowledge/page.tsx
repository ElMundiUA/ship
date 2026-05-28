import { redirect } from "next/navigation";

import { ApiUnavailable } from "@/components/api-unavailable";
import { PageBody, PageHeader } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import {
  type ApiActivatedRepo,
  type ApiImporterType,
  type ApiKnowledgeCorpus,
  type ApiWorkspaceImporter,
  type ApiWorkspaceImporterIntegration,
  ApiHttpError,
  getWorkspaceCorpus,
  listActivatedRepos,
  listImporterTypes,
  listWorkspaceImporterIntegrations,
  listWorkspaceImporters,
} from "@/lib/api/client";
import {
  getCachedMe,
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

import { KnowledgeControlCenter } from "./knowledge-control-center";

export const dynamic = "force-dynamic";

type LiveData = {
  workspace: { id: string; slug: string; name: string };
  repos: ApiActivatedRepo[];
  corpus: ApiKnowledgeCorpus | null;
  importers: ApiWorkspaceImporter[];
  importerTypes: ApiImporterType[];
  importerIntegrations: ApiWorkspaceImporterIntegration[];
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
    const [
      repos,
      me,
      corpus,
      importers,
      importerTypes,
      importerIntegrations,
    ] = await Promise.all([
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
      getCachedMe(),
      getWorkspaceCorpus(workspace.id, token).catch(
        () => null as ApiKnowledgeCorpus | null,
      ),
      // Importers + types are best-effort: Lighthouse may be unconfigured.
      listWorkspaceImporters(workspace.id, token).catch(
        () => [] as ApiWorkspaceImporter[],
      ),
      listImporterTypes(workspace.id, token).catch(
        () => [] as ApiImporterType[],
      ),
      // Auto-resolvable workspace integrations (GitHub App, Notion / Linear).
      listWorkspaceImporterIntegrations(workspace.id, token).catch(
        () => [] as ApiWorkspaceImporterIntegration[],
      ),
    ]);

    return {
      status: "live",
      data: {
        workspace: {
          id: workspace.id,
          slug: workspace.slug,
          name: workspace.name,
        },
        repos,
        corpus,
        importers,
        importerTypes,
        importerIntegrations,
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

  const {
    workspace,
    repos,
    corpus,
    importers,
    importerTypes,
    importerIntegrations,
    me,
  } = result.data;

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
          corpus={corpus}
          importers={importers}
          importerTypes={importerTypes}
          importerIntegrations={importerIntegrations}
        />
      </PageBody>
    </>
  );
}
