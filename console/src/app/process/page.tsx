import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader, MockBanner } from "@/components/ui";
import {
  type ProcessConfigSource,
  processFromRepoConfig,
  selectConfigSource,
} from "./process-config";
import { ProcessEditorWorkspace } from "./process-editor-workspace";
import { RepoSelector } from "./repo-selector";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiActivatedRepo,
  type ApiProcess,
  type ApiProcessState,
  type ApiRepoConfig,
  getRepoConfig,
  getProcess,
  isApiConfigured,
  listActivatedRepos,
  listProcesses,
  listWorkspaces,
} from "@/lib/api/client";
import type { ApiWorkspace } from "@/lib/api/types";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

type SearchParams = { [key: string]: string | string[] | undefined };

export default async function ProcessPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParams>;
}) {
  const params = (await searchParams) ?? {};
  const selectedStateId =
    typeof params.state === "string" ? params.state : undefined;
  const selectedTab = params.tab === "routines" ? "routines" : "process";
  const selectedRepoId =
    typeof params.repo === "string" ? params.repo : undefined;
  const reason = typeof params.reason === "string" ? params.reason : undefined;

  if (!isApiConfigured()) {
    return renderProcessPage({
      workspace: { id: "mock", name: "Mock workspace", slug: "mock" },
      process: mockProcess,
      repos: mockRepos,
      selectedRepo: mockRepos[0],
      selectedStateId,
      selectedTab,
      reason,
      mock: true,
    });
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fprocess");

  const result = await loadLiveProcess(token, selectedRepoId);
  if (result === "unauthorized") redirect("/login?next=%2Fprocess");
  if (result === "empty") redirect("/onboarding?step=github");
  if (result === "down") return renderDownState();

  return renderProcessPage({ ...result, selectedStateId, selectedTab, reason });
}

type LiveProcess = {
  workspace: ApiWorkspace;
  process: ApiProcess;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  config: ApiRepoConfig | null;
  configSource: ProcessConfigSource;
};

async function loadLiveProcess(
  token: string,
  selectedRepoId?: string,
): Promise<LiveProcess | "empty" | "unauthorized" | "down"> {
  let workspaces: ApiWorkspace[];
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
  if (workspaces.length === 0) return "empty";

  const workspace = workspaces[0];
  try {
    const [processList, repos] = await Promise.all([
      listProcesses(workspace.id, token),
      listActivatedRepos(workspace.id, token).catch(() => [] as ApiActivatedRepo[]),
    ]);
    const processId = processList.primary_process_id || "development";
    const selectedRepo =
      repos.find((repo) => repo.id === selectedRepoId) ?? repos[0] ?? null;
    const [projectedProcess, config] = await Promise.all([
      getProcess(workspace.id, processId, token, {
        repoId: selectedRepo?.id,
      }),
      selectedRepo
        ? getRepoConfig(workspace.id, selectedRepo.id, token).catch(
            () => null as ApiRepoConfig | null,
          )
        : Promise.resolve(null),
    ]);
    const repoProcess = processFromRepoConfig(config, projectedProcess);
    const process = repoProcess ?? projectedProcess;
    const configSource = selectConfigSource(config, repoProcess);
    return {
      workspace,
      process,
      repos,
      selectedRepo,
      config,
      configSource,
    };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderProcessPage({
  workspace,
  process,
  repos,
  selectedRepo,
  config,
  configSource,
  selectedStateId,
  selectedTab,
  reason,
  mock = false,
}: {
  workspace: Pick<ApiWorkspace, "id" | "name" | "slug">;
  process: ApiProcess;
  repos: ApiActivatedRepo[];
  selectedRepo: ApiActivatedRepo | null;
  config?: ApiRepoConfig | null;
  configSource?: ProcessConfigSource;
  selectedStateId?: string;
  selectedTab: "process" | "routines";
  reason?: string;
  mock?: boolean;
}) {
  return (
    <AppShell
      title="Process"
      kicker={workspace.slug}
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      actions={
        <>
          <Link
            href="/inbox"
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            Inbox
          </Link>
        </>
      }
    >
      {mock && <MockBanner />}
      <div className="space-y-3">
        <RepoSelector repos={repos} selectedRepo={selectedRepo} />
        <ProcessNotice reason={reason} />
        <ConfigSourceBanner config={config ?? null} source={configSource ?? "fallback"} />
        {selectedTab === "routines" ? (
          <>
            <ProcessTabs selected={selectedTab} repoId={selectedRepo?.id} />
            <RoutinesPanel process={process} />
          </>
        ) : (
          <ProcessEditorWorkspace
            workspaceId={workspace.id}
            process={process}
            selectedStateId={selectedStateId}
            repoId={selectedRepo?.id}
            config={config ?? null}
            tabs={
              <ProcessTabs
                selected={selectedTab}
                repoId={selectedRepo?.id}
                variant="inline"
              />
            }
          />
        )}
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
  repoId,
  variant = "card",
}: {
  selected: "process" | "routines";
  repoId?: string;
  variant?: "card" | "inline";
}) {
  const processHref = repoId ? `/process?repo=${encodeURIComponent(repoId)}` : "/process";
  const routinesHref = repoId
    ? `/process?tab=routines&repo=${encodeURIComponent(repoId)}`
    : "/process?tab=routines";
  if (variant === "inline") {
    return (
      <div className="flex flex-wrap items-center gap-1">
        <TabLink href={processHref} active={selected === "process"}>
          Process canvas
        </TabLink>
        <TabLink href={routinesHref} active={selected === "routines"}>
          Routines
        </TabLink>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.035] p-2">
      <div className="flex gap-1">
        <TabLink href={processHref} active={selected === "process"}>
          Process canvas
        </TabLink>
        <TabLink href={routinesHref} active={selected === "routines"}>
          Routines
        </TabLink>
      </div>
      <p className="hidden text-xs text-white/45 md:block">
        Runtime metrics stay in dashboard/analytics; this page is for editing the
        flow.
      </p>
    </div>
  );
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

function RoutinesPanel({ process }: { process: ApiProcess }) {
  return (
    <Card>
      <CardHeader
        title="Routines"
        subtitle="Supporting recurring work. These are not task FSM states."
      />
      {process.routines.length === 0 ? (
        <p className="text-sm text-white/50">No routines projected yet.</p>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {process.routines.map((routine) => (
            <div
              key={routine.id}
              className="rounded-xl border border-white/10 bg-white/[0.035] p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-white">
                    {routine.name}
                  </div>
                  <div className="mt-1 text-xs text-white/50">
                    {routine.specialist_name}
                  </div>
                </div>
                <Badge tone={routine.status === "failed" ? "err" : "neutral"}>
                  {routine.status ?? "idle"}
                </Badge>
              </div>
              <div className="mt-3 text-xs text-white/45">
                Schedule:{" "}
                <span className="text-white/65">
                  {routine.schedule ?? "not configured"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function renderDownState() {
  return (
    <AppShell title="Process">
      <Card>
        <CardHeader
          title="Backend unreachable"
          subtitle="The process view couldn't load live orchestration data."
        />
        <p className="text-sm text-white/70">
          Try again in a few seconds. If this keeps happening, check the
          backend service in the dev Ship environment.
        </p>
      </Card>
    </AppShell>
  );
}

const mockProcess: ApiProcess = {
  id: "development",
  name: "Development Process",
  primary: true,
  state_count: 5,
  task_count: 0,
  blocked_count: 0,
  health: "ok",
  specialists: [],
  states: [
    mockState("task_intake", "Intake", "Intake specialist", "ok", 1, 0),
    mockState("ba_requirements", "Requirements", "Business analyst", "ok", 0, 0),
    mockState("dev_implementation", "Implementation", "Developer", "ok", 0, 0),
    mockState("qa_manual", "Quality Review", "QA engineer", "ok", 0, 0),
    mockState("pr_review", "Final Review", "Review owner", "ok", 1, 0),
  ],
  transitions: [],
  tasks: [],
  routines: [
    {
      id: "self_heal",
      name: "Self Heal",
      specialist_id: "devops_platform",
      specialist_name: "DevOps/platform",
      schedule: "0 * * * *",
      instructions: "Check execution health.",
      last_run: null,
      status: "idle",
    },
  ],
  process_graph: { links: [] },
  adapter_diagnostics: [
    {
      kind: "tracker",
      name: "Tracker adapter",
      status: "unknown",
      message: "Mock projection uses Ship-managed state.",
      capabilities: ["shadow_state"],
    },
    {
      kind: "runner",
      name: "Runner adapter",
      status: "ok",
      message: "Execution windows are projected into the process.",
      capabilities: ["execution_windows"],
    },
    {
      kind: "agent",
      name: "Agent profile",
      status: "unknown",
      message: "Agent profile selection lands in a later phase.",
      capabilities: ["auto_select"],
    },
  ],
};

const mockRepos: ApiActivatedRepo[] = [
  {
    id: "mock-repo",
    external_id: 1,
    full_name: "helio/app",
    default_branch: "main",
    private: true,
    html_url: "https://github.com/helio/app",
    description: "Mock repository",
    activated_at: new Date().toISOString(),
    provider: "github",
    preset: "default",
    installed_bundle_version: 1,
    current_bundle_version: 1,
  },
];

function mockState(
  id: string,
  name: string,
  specialistName: string,
  health: ApiProcess["health"],
  taskCount: number,
  blockedCount: number,
): ApiProcessState {
  return {
    id,
    name,
    specialist_id: specialistName.toLowerCase().replaceAll(" ", "_"),
    specialist_name: specialistName,
    instructions:
      "Execute this state using task context and runtime pattern discovery.",
    triggers: [{ type: "manual", interval: null, event: null }],
    exit_conditions: [{ expression: "state_complete == true" }],
    block_conditions: [{ expression: "requires_human_input == true" }],
    runtime: {
      task_count: taskCount,
      blocked_count: blockedCount,
      last_execution_time: new Date().toISOString(),
      health,
    },
  };
}
