import Link from "next/link";
import { redirect } from "next/navigation";
import { Suspense } from "react";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import { ApiUnavailable } from "@/components/api-unavailable";
import {
  type ProcessConfigSource,
  processFromRepoConfig,
  selectConfigSource,
} from "./process-config";
import { FlowSchedulePanel } from "./flow-schedule-panel";
import { ProcessGraphOverview } from "./process-graph-overview";
import { ProcessEditorWorkspace } from "./process-editor-workspace";
import { RoutinesPanel } from "./routines-panel";
import { TrackerMappingPanel } from "./tracker-mapping-panel";
import { RepoSelector } from "./repo-selector";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiActivatedRepo,
  type ApiNativeIntegration,
  type ApiProcess,
  type ApiProcessList,
  type ApiProcessState,
  type ApiRepoConfig,
  getRepoConfig,
  getProcess,
  isApiConfigured,
  listActivatedRepos,
  listIntegrations,
  listNativeIntegrations,
  listProcesses,
  listWorkspaces,
} from "@/lib/api/client";
import {
  EditorLockedBanner,
  isEditorLocked,
  type EditorPrereqStatus,
} from "./editor-locked-banner";
import type { ApiIntegration, ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace, toAppShellWorkspaces } from "@/lib/workspace-scope";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | string[] | undefined };
type ProcessTab = "flow" | "schedule" | "routines" | "mapping";

export default async function ProcessPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const params = (await searchParams) ?? {};
  const selectedStateId =
    typeof params.state === "string" ? params.state : undefined;
  const selectedTab = parseProcessTab(params.tab);
  const selectedRepoId =
    typeof params.repo === "string" ? params.repo : undefined;
  const explicitProcessId =
    typeof params.process === "string" ? params.process : undefined;
  const selectedProcessId =
    explicitProcessId || (params.tab || params.state ? "development" : undefined);
  const reason = typeof params.reason === "string" ? params.reason : undefined;

  if (!isApiConfigured()) {
    return renderDownState("SHIP_API_URL is not set on this deployment.");
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fprocess&reason=session_expired");

  if (!selectedProcessId) {
    const graphResult = await loadLiveProcessGraph(token, selectedRepoId, params);
    if (graphResult === "unauthorized") redirect("/login?next=%2Fprocess&reason=session_expired");
    if (graphResult === "empty") redirect("/onboarding?step=github");
    if (graphResult === "down") return renderDownState();
    return renderProcessGraphPage(graphResult);
  }

  const shell = await loadProcessShell(token, selectedProcessId, selectedRepoId, params);
  if (shell === "unauthorized") redirect("/login?next=%2Fprocess&reason=session_expired");
  if (shell === "empty") redirect("/onboarding?step=github");
  if (shell === "down") return renderDownState();

  return renderProcessPage({ ...shell, selectedStateId, selectedTab, reason, token });
}

type ProcessShell = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  processList: ApiProcessList;
  resolvedProcessId: string;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  prereqStatus: EditorPrereqStatus;
};

type LiveProcessGraph = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  processList: ApiProcessList;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
};

/**
 * Fast data load — workspaces, repo list, the process catalogue, and
 * the prereq integrations. Everything here is a quick listing call;
 * the heavy ``getProcess`` + ``getRepoConfig`` round-trips run inside
 * the streamed ``<EditorContent>`` so the shell can paint immediately.
 */
async function loadProcessShell(
  token: string,
  processId: string,
  selectedRepoId: string | undefined,
  searchParams: SearchParams,
): Promise<ProcessShell | "empty" | "unauthorized" | "down"> {
  let workspaces: ApiWorkspace[];
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (workspaces.length === 0) return "empty";

  const resolved = await getResolvedWorkspaceId(searchParams, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);
  try {
    const [processList, repos, integrations, nativeIntegrations] =
      await Promise.all([
        listProcesses(workspace.id, token),
        listActivatedRepos(workspace.id, token).catch(
          () => [] as ApiActivatedRepo[],
        ),
        listIntegrations(workspace.id, token).catch(
          () => [] as ApiIntegration[],
        ),
        listNativeIntegrations(workspace.id, token).catch(
          () => [] as ApiNativeIntegration[],
        ),
      ]);
    const resolvedProcessId =
      processList.processes.some((process) => process.id === processId)
        ? processId
        : processList.primary_process_id || "development";
    const selectedRepo =
      repos.find((repo) => repo.id === selectedRepoId) ?? repos[0] ?? null;
    const prereqStatus = computePrereqStatus(
      workspace,
      integrations,
      nativeIntegrations,
    );
    return {
      workspace,
      allWorkspaces: workspaces,
      processList,
      resolvedProcessId,
      repos,
      selectedRepo,
      prereqStatus,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

async function loadLiveProcessGraph(
  token: string,
  selectedRepoId: string | undefined,
  searchParams: SearchParams,
): Promise<LiveProcessGraph | "empty" | "unauthorized" | "down"> {
  let workspaces: ApiWorkspace[];
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (workspaces.length === 0) return "empty";

  const resolved = await getResolvedWorkspaceId(searchParams, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);
  try {
    const [processList, repos] = await Promise.all([
      listProcesses(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
    ]);
    return {
      workspace,
      allWorkspaces: workspaces,
      processList,
      repos,
      selectedRepo: repos.find((repo) => repo.id === selectedRepoId) ?? repos[0] ?? null,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderProcessGraphPage({
  workspace,
  allWorkspaces,
  processList,
  repos,
  selectedRepo,
}: {
  workspace: Pick<ApiWorkspace, "id" | "name" | "slug">;
  allWorkspaces?: ApiWorkspace[];
  processList: ApiProcessList;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
}) {
  return (
    <AppShell
      title="Process graph"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={
        allWorkspaces && allWorkspaces.length > 0
          ? toAppShellWorkspaces(allWorkspaces)
          : undefined
      }
    >
      <div className="space-y-3">
        <RepoSelector repos={repos} selectedRepo={selectedRepo} />
        <ProcessGraphOverview processList={processList} repoId={selectedRepo?.id} />
      </div>
    </AppShell>
  );
}

function renderProcessPage({
  workspace,
  allWorkspaces,
  resolvedProcessId,
  repos,
  selectedRepo,
  selectedStateId,
  selectedTab,
  reason,
  prereqStatus,
  token,
}: {
  workspace: ApiWorkspace;
  allWorkspaces?: ApiWorkspace[];
  resolvedProcessId: string;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  selectedStateId?: string;
  selectedTab: ProcessTab;
  reason?: string;
  prereqStatus: EditorPrereqStatus;
  token: string;
}) {
  const locked = isEditorLocked(prereqStatus);
  const multiWs = (allWorkspaces?.length ?? 0) > 1;
  return (
    <AppShell
      title="Process"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={
        allWorkspaces && allWorkspaces.length > 0
          ? toAppShellWorkspaces(allWorkspaces)
          : undefined
      }
    >
      <div className="space-y-3">
        <RepoSelector
          repos={repos}
          selectedRepo={selectedRepo}
          processId={resolvedProcessId}
        />
        <ProcessNotice reason={reason} />
        {locked && (
          <EditorLockedBanner
            workspaceId={workspace.id}
            multiWorkspace={multiWs}
            status={prereqStatus}
          />
        )}
        <ProcessTabs
          selected={selectedTab}
          processId={resolvedProcessId}
          repoId={selectedRepo?.id}
        />
        <Suspense
          key={`${resolvedProcessId}:${selectedRepo?.id ?? "none"}:${selectedTab}`}
          fallback={<EditorContentSkeleton />}
        >
          <EditorContent
            workspaceId={workspace.id}
            processId={resolvedProcessId}
            repoId={selectedRepo?.id}
            selectedTab={selectedTab}
            selectedStateId={selectedStateId}
            locked={locked}
            token={token}
          />
        </Suspense>
      </div>
    </AppShell>
  );
}

function ProcessNotice({ reason }: { reason?: string }) {
  if (!reason) return null;
  const message = noticeMessage(reason);
  const isError = reason !== "pr_opened";
  return (
    <div
      className={
        isError
          ? "rounded-2xl border border-coral/25 bg-coral/[0.05] px-4 py-2 text-xs text-coral/90"
          : "rounded-2xl border border-aqua/25 bg-aqua/[0.05] px-4 py-2 text-xs text-aqua/90"
      }
    >
      {message}
    </div>
  );
}

function noticeMessage(reason: string): string {
  if (reason === "bad_request") return "Process save could not start: missing repository or workspace.";
  if (reason === "bad_json") return "Process save failed because the submitted config payload was malformed.";
  if (reason === "state_not_found") return "Process save failed because the selected state is no longer in the config.";
  if (reason === "api_unavailable") return "Backend is unreachable. Try again after the API is back.";
  if (reason === "http_409") return ".ship/config.yml changed since the editor loaded. Reload before saving.";
  if (reason === "http_422") return "Process config is invalid. Check state ids, transitions, and layout values.";
  if (reason.startsWith("http_")) return `Process save failed (${reason.replace("http_", "HTTP ")}).`;
  return "Process save failed. Please retry.";
}

function ConfigSourceBanner({
  config,
  source,
}: {
  config: ApiRepoConfig | null;
  source: ProcessConfigSource;
}) {
  if (source === "repo-process") {
    return (
      <div className="rounded-2xl border border-aqua/25 bg-aqua/[0.05] px-4 py-2 text-xs text-aqua/90">
        Editing process from <code className="font-mono">.ship/config.yml</code>
        {config?.sha ? ` · ${config.sha.slice(0, 7)}` : ""}.
      </div>
    );
  }
  if (source === "repo-lanes") {
    return (
      <div className="rounded-2xl border border-amber-300/25 bg-amber-300/[0.06] px-4 py-2 text-xs text-amber-100/90">
        This repo has the old <code className="font-mono">lanes:</code> config,
        not a <code className="font-mono">process:</code> section yet. The canvas
        shows Ship&apos;s development process template until this repo is reseeded or
        migrated.
      </div>
    );
  }
  if (source === "missing") {
    return (
      <div className="rounded-2xl border border-coral/25 bg-coral/[0.05] px-4 py-2 text-xs text-coral/90">
        This repo has no <code className="font-mono">.ship/config.yml</code> on
        its default branch yet. Reseed the repo to create one.
      </div>
    );
  }
  return null;
}

function ProcessTabs({
  selected,
  processId,
  repoId,
}: {
  selected: ProcessTab;
  processId: string;
  repoId?: string;
}) {
  const hrefFor = (tab: ProcessTab) => {
    const query = new URLSearchParams();
    if (tab !== "flow") query.set("tab", tab);
    if (repoId) query.set("repo", repoId);
    const suffix = query.toString();
    return suffix
      ? `/process/${encodeURIComponent(processId)}?${suffix}`
      : `/process/${encodeURIComponent(processId)}`;
  };
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.035] p-2">
      <div className="flex gap-1">
        <TabLink href={hrefFor("flow")} active={selected === "flow"}>
          Flow
        </TabLink>
        <TabLink href={hrefFor("schedule")} active={selected === "schedule"}>
          Flow schedule
        </TabLink>
        <TabLink href={hrefFor("routines")} active={selected === "routines"}>
          Routines
        </TabLink>
        <TabLink href={hrefFor("mapping")} active={selected === "mapping"}>
          Tracker mapping
        </TabLink>
      </div>
      <p className="hidden text-xs text-white/45 md:block">
        One development process: flow, capacity, standalone routines, and tracker states.
      </p>
    </div>
  );
}

function parseProcessTab(value: string | string[] | undefined): ProcessTab {
  if (value === "schedule") return "schedule";
  if (value === "routines") return "routines";
  if (value === "mapping" || value === "tracker") return "mapping";
  return "flow";
}

function TabLink({
  href,
  active,
  children,
}: {
  href: string;
  active: boolean;
  children: string;
}) {
  return (
    <Link
      href={href}
      className={[
        "rounded-xl px-3 py-1.5 text-xs font-semibold transition",
        active
          ? "bg-aqua/15 text-aqua"
          : "text-white/55 hover:bg-white/[0.05] hover:text-white",
      ].join(" ")}
    >
      {children}
    </Link>
  );
}

function renderDownState(details?: string) {
  return (
    <AppShell title="Process">
      <ApiUnavailable scope="process" details={details} />
    </AppShell>
  );
}

/**
 * Slow-path content: ``getProcess`` (state graph projection) and the
 * repo's ``.ship/config.yml`` resolve here. Wrapped in <Suspense>
 * upstream so the rest of the page paints instantly.
 */
async function EditorContent({
  workspaceId,
  processId,
  repoId,
  selectedTab,
  selectedStateId,
  locked,
  token,
}: {
  workspaceId: string;
  processId: string;
  repoId: string | undefined;
  selectedTab: ProcessTab;
  selectedStateId?: string;
  locked: boolean;
  token: string;
}) {
  let projectedProcess: ApiProcess;
  let config: ApiRepoConfig | null = null;
  try {
    [projectedProcess, config] = await Promise.all([
      getProcess(workspaceId, processId, token, { repoId }),
      repoId
        ? getRepoConfig(workspaceId, repoId, token).catch(
            () => null as ApiRepoConfig | null,
          )
        : Promise.resolve(null),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fprocess&reason=session_expired");
    }
    return <ApiUnavailable scope="process" />;
  }
  const repoProcess = processFromRepoConfig(config, projectedProcess);
  const process = repoProcess ?? projectedProcess;
  const configSource = selectConfigSource(config, repoProcess);
  return (
    <>
      <ConfigSourceBanner config={config} source={configSource} />
      <fieldset
        disabled={locked}
        aria-disabled={locked}
        className={[
          "border-0 p-0 m-0 min-w-0",
          locked ? "pointer-events-none select-none opacity-60" : "",
        ].join(" ")}
      >
        {selectedTab === "flow" ? (
          <ProcessEditorWorkspace
            workspaceId={workspaceId}
            process={process}
            selectedStateId={selectedStateId}
            repoId={repoId}
            config={config}
          />
        ) : selectedTab === "schedule" ? (
          <FlowSchedulePanel
            workspaceId={workspaceId}
            process={process}
            repoId={repoId}
            config={config}
          />
        ) : selectedTab === "mapping" ? (
          <TrackerMappingPanel
            workspaceId={workspaceId}
            process={process}
            repoId={repoId}
            config={config}
          />
        ) : (
          <RoutinesPanel
            workspaceId={workspaceId}
            process={process}
            repoId={repoId}
            config={config}
          />
        )}
      </fieldset>
    </>
  );
}

function EditorContentSkeleton() {
  return (
    <div className="space-y-3">
      <div className="h-9 animate-pulse rounded-2xl border border-white/10 bg-white/[0.04]" />
      <div className="h-[480px] animate-pulse rounded-3xl border border-white/10 bg-gradient-to-br from-white/[0.04] via-white/[0.02] to-transparent">
        <div className="flex h-full items-center justify-center text-xs font-semibold uppercase tracking-widest text-white/30">
          Loading process…
        </div>
      </div>
    </div>
  );
}

const TRACKER_KINDS = new Set(["linear", "jira", "github"]);

function computePrereqStatus(
  workspace: ApiWorkspace,
  integrations: ApiIntegration[],
  nativeIntegrations: ApiNativeIntegration[],
): EditorPrereqStatus {
  const trackers = integrations.filter(
    (i) => TRACKER_KINDS.has(i.kind) && i.status === "ok",
  );
  const orchestrators = nativeIntegrations.filter(
    (n) => n.capabilities.includes("orchestrator") && n.status === "ready",
  );
  const agent = workspace.default_agent_profile?.trim() ?? "";
  return {
    tracker: trackers.length > 0
      ? {
          ok: true,
          detail: `Connected: ${trackers.map((t) => t.kind).join(", ")}`,
        }
      : {
          ok: false,
          detail: "No active Linear / Jira / GitHub Issues binding for this workspace.",
        },
    orchestrator: orchestrators.length > 0
      ? {
          ok: true,
          detail: `Ready: ${orchestrators.map((o) => o.provider).join(", ")}`,
        }
      : {
          ok: false,
          detail: "No CI orchestrator (GitHub Actions / Azure DevOps) ready for this workspace.",
        },
    default_agent: agent
      ? { ok: true, detail: `Workspace default: ${agent}` }
      : {
          ok: false,
          detail: "Workspace hasn't picked a default agent profile yet.",
        },
  };
}
