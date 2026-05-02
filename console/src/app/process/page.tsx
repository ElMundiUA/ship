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
  type ApiProcessSummary,
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

  const summary = shell.processList.processes.find(
    (p) => p.id === shell.resolvedProcessId,
  );
  return renderProcessPage({
    ...shell,
    summary: summary ?? null,
    selectedStateId,
    selectedTab,
    reason,
    token,
  });
}

type ProcessShell = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  processList: ApiProcessList;
  resolvedProcessId: string;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  prereqStatus: EditorPrereqStatus;
  /** First active tracker integration's kind (linear/jira/github/...). */
  trackerKind: string | null;
};

type LiveProcessGraph = {
  workspace: ApiWorkspace;
  allWorkspaces: ApiWorkspace[];
  processList: ApiProcessList;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  prereqStatus: EditorPrereqStatus;
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
    const trackerIntegration = integrations.find(
      (i) => TRACKER_KINDS.has(i.kind) && i.status === "ok",
    );
    return {
      workspace,
      allWorkspaces: workspaces,
      processList,
      resolvedProcessId,
      repos,
      selectedRepo,
      prereqStatus,
      trackerKind: trackerIntegration?.kind ?? null,
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
    const prereqStatus = computePrereqStatus(
      workspace,
      integrations,
      nativeIntegrations,
    );
    return {
      workspace,
      allWorkspaces: workspaces,
      processList,
      repos,
      selectedRepo: repos.find((repo) => repo.id === selectedRepoId) ?? repos[0] ?? null,
      prereqStatus,
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
  prereqStatus,
}: {
  workspace: ApiWorkspace;
  allWorkspaces?: ApiWorkspace[];
  processList: ApiProcessList;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  prereqStatus: EditorPrereqStatus;
}) {
  const locked = isEditorLocked(prereqStatus);
  const multiWs = (allWorkspaces?.length ?? 0) > 1;
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
        {locked && (
          <EditorLockedBanner
            workspaceId={workspace.id}
            multiWorkspace={multiWs}
            status={prereqStatus}
          />
        )}
        <ProcessGraphOverview processList={processList} repoId={selectedRepo?.id} />
      </div>
    </AppShell>
  );
}

function renderProcessPage({
  workspace,
  allWorkspaces,
  resolvedProcessId,
  summary,
  repos,
  selectedRepo,
  selectedStateId,
  selectedTab,
  reason,
  prereqStatus,
  trackerKind,
  token,
}: {
  workspace: ApiWorkspace;
  allWorkspaces?: ApiWorkspace[];
  resolvedProcessId: string;
  summary: ApiProcessSummary | null;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  selectedStateId?: string;
  selectedTab: ProcessTab;
  reason?: string;
  prereqStatus: EditorPrereqStatus;
  trackerKind: string | null;
  token: string;
}) {
  const locked = isEditorLocked(prereqStatus);
  const multiWs = (allWorkspaces?.length ?? 0) > 1;
  const title = summary?.name?.trim() || "Process";
  return (
    <AppShell
      title={title}
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      allWorkspaces={
        allWorkspaces && allWorkspaces.length > 0
          ? toAppShellWorkspaces(allWorkspaces)
          : undefined
      }
    >
      <div className="space-y-3">
        <ProcessHeaderCard
          summary={summary}
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
            trackerKind={trackerKind}
            token={token}
          />
        </Suspense>
      </div>
    </AppShell>
  );
}

function ProcessHeaderCard({
  summary,
  repos,
  selectedRepo,
  processId,
}: {
  summary: ApiProcessSummary | null;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  processId: string;
}) {
  if (!summary) {
    return (
      <RepoSelector
        repos={repos}
        selectedRepo={selectedRepo}
        processId={processId}
      />
    );
  }
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
      <div className="min-w-0">
        {summary.description ? (
          <p className="max-w-2xl text-sm text-white/65">{summary.description}</p>
        ) : (
          <p className="text-sm italic text-white/45">
            No description set for this process yet.
          </p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
            {summary.state_count} state{summary.state_count === 1 ? "" : "s"}
          </span>
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-1">
            {summary.task_count} active task{summary.task_count === 1 ? "" : "s"}
          </span>
          {summary.blocked_count > 0 && (
            <span className="rounded-full border border-coral/30 bg-coral/[0.08] px-2 py-1 text-coral/85">
              {summary.blocked_count} blocked
            </span>
          )}
          <span
            className={[
              "rounded-full px-2 py-1",
              summary.health === "ok"
                ? "border border-emerald-400/30 bg-emerald-400/10 text-emerald-300"
                : summary.health === "degraded"
                  ? "border border-amber-400/30 bg-amber-400/10 text-amber-200"
                  : "border border-coral/30 bg-coral/10 text-coral",
            ].join(" ")}
          >
            {summary.health}
          </span>
        </div>
      </div>
      <div className="shrink-0">
        <RepoSelector
          repos={repos}
          selectedRepo={selectedRepo}
          processId={processId}
        />
      </div>
    </div>
  );
}

function ProcessNotice({ reason }: { reason?: string }) {
  if (!reason) return null;
  const message = noticeMessage(reason);
  const isError = reason !== "pr_opened";
  return (
    <div
      className={[
        "flex items-start gap-2 rounded-xl border px-3 py-2 text-xs",
        isError
          ? "border-coral/30 bg-coral/[0.06] text-coral/90"
          : "border-aqua/30 bg-aqua/[0.06] text-aqua/90",
      ].join(" ")}
    >
      <span
        aria-hidden
        className={[
          "mt-0.5 h-1.5 w-1.5 shrink-0 rounded-full",
          isError ? "bg-coral" : "bg-aqua",
        ].join(" ")}
      />
      <span>{message}</span>
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
  // One-line pill, lives above the editor body and stays out of the way.
  if (source === "repo-process") {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-aqua/25 bg-aqua/[0.06] px-3 py-1 text-[11px] font-semibold text-aqua/90">
        <span className="h-1.5 w-1.5 rounded-full bg-aqua" aria-hidden />
        From <code className="font-mono">.ship/config.yml</code>
        {config?.sha ? <span className="text-aqua/65">· {config.sha.slice(0, 7)}</span> : null}
      </div>
    );
  }
  if (source === "repo-lanes") {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-amber-300/30 bg-amber-300/[0.07] px-3 py-1 text-[11px] font-semibold text-amber-200">
        <span className="h-1.5 w-1.5 rounded-full bg-amber-300" aria-hidden />
        Legacy <code className="font-mono">lanes:</code> config — reseed to migrate.
      </div>
    );
  }
  if (source === "missing") {
    return (
      <div className="inline-flex items-center gap-2 rounded-full border border-coral/30 bg-coral/[0.07] px-3 py-1 text-[11px] font-semibold text-coral">
        <span className="h-1.5 w-1.5 rounded-full bg-coral" aria-hidden />
        No <code className="font-mono">.ship/config.yml</code> on default branch — reseed required.
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
    <div className="flex items-center gap-1 rounded-2xl border border-white/10 bg-white/[0.035] p-2">
      <TabLink href={hrefFor("flow")} active={selected === "flow"}>
        Flow
      </TabLink>
      <TabLink href={hrefFor("schedule")} active={selected === "schedule"}>
        Capacity
      </TabLink>
      <TabLink href={hrefFor("routines")} active={selected === "routines"}>
        Routines
      </TabLink>
      <TabLink href={hrefFor("mapping")} active={selected === "mapping"}>
        Tracker
      </TabLink>
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
  trackerKind,
  token,
}: {
  workspaceId: string;
  processId: string;
  repoId: string | undefined;
  selectedTab: ProcessTab;
  selectedStateId?: string;
  locked: boolean;
  trackerKind: string | null;
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
            trackerKind={trackerKind ?? undefined}
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
