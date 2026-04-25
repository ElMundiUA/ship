import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, type BadgeTone, Card, CardHeader, MockBanner } from "@/components/ui";
import { ProcessCanvasEditor } from "./process-canvas-editor";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiProcess,
  type ApiProcessState,
  type ApiProcessTask,
  getProcess,
  isApiConfigured,
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
  const editMode = params.mode === "edit";

  if (!isApiConfigured()) {
    return renderProcessPage({
      workspace: { id: "mock", name: "Mock workspace", slug: "mock" },
      process: mockProcess,
      selectedStateId,
      selectedTab,
      editMode,
      mock: true,
    });
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fprocess");

  const result = await loadLiveProcess(token);
  if (result === "unauthorized") redirect("/login?next=%2Fprocess");
  if (result === "empty") redirect("/onboarding?step=github");
  if (result === "down") return renderDownState();

  return renderProcessPage({ ...result, selectedStateId, selectedTab, editMode });
}

type LiveProcess = {
  workspace: ApiWorkspace;
  process: ApiProcess;
};

async function loadLiveProcess(
  token: string,
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
    const processList = await listProcesses(workspace.id, token);
    const processId = processList.primary_process_id || "development";
    const process = await getProcess(workspace.id, processId, token);
    return { workspace, process };
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) return "unauthorized";
    if (err instanceof ApiUnavailableError) return "down";
    return "down";
  }
}

function renderProcessPage({
  workspace,
  process,
  selectedStateId,
  selectedTab,
  editMode,
  mock = false,
}: {
  workspace: Pick<ApiWorkspace, "id" | "name" | "slug">;
  process: ApiProcess;
  selectedStateId?: string;
  selectedTab: "process" | "routines";
  editMode: boolean;
  mock?: boolean;
}) {
  const selectedState =
    process.states.find((state) => state.id === selectedStateId) ??
    process.states[0];
  const selectedTasks = process.tasks.filter(
    (task) => task.state_id === selectedState?.id,
  );

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
        <ProcessTabs selected={selectedTab} />
        {selectedTab === "routines" ? (
          <RoutinesPanel process={process} />
        ) : (
          <section className="relative min-h-[680px]">
            <ProcessCanvasEditor
              process={process}
              selectedStateId={selectedState?.id}
              editMode={editMode}
            />
            <StateDetails state={selectedState} tasks={selectedTasks} />
          </section>
        )}
      </div>
    </AppShell>
  );
}

function ProcessTabs({ selected }: { selected: "process" | "routines" }) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-white/10 bg-white/[0.035] p-2">
      <div className="flex gap-1">
        <TabLink href="/process" active={selected === "process"}>
          Process canvas
        </TabLink>
        <TabLink href="/process?tab=routines" active={selected === "routines"}>
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

function StateDetails({
  state,
  tasks,
}: {
  state?: ApiProcessState;
  tasks: ApiProcessTask[];
}) {
  if (!state) {
    return (
      <Card>
        <CardHeader title="State details" subtitle="No state selected." />
      </Card>
    );
  }
  return (
    <aside className="mt-3 space-y-3 xl:absolute xl:right-4 xl:top-4 xl:mt-0 xl:w-[360px]">
      <Card>
        <CardHeader
          title={state.name}
          subtitle={`Assigned to ${state.specialist_name}`}
        />
        <div className="space-y-4 text-sm text-white/70">
          <DetailBlock title="Instructions">{state.instructions}</DetailBlock>
          <DetailList
            title="Triggers"
            items={state.triggers.map((trigger) =>
              trigger.type === "schedule"
                ? `schedule: ${trigger.interval ?? "not configured"}`
                : trigger.type === "event"
                  ? `event: ${trigger.event ?? "event"}`
                  : "manual",
            )}
          />
          <DetailList
            title="Exit conditions"
            items={state.exit_conditions.map((item) => item.expression)}
          />
          <DetailList
            title="Block conditions"
            items={state.block_conditions.map((item) => item.expression)}
          />
        </div>
      </Card>
      <Card>
        <CardHeader
          title="State tasks"
          subtitle="Side-panel context for the selected state."
        />
        {tasks.length === 0 ? (
          <p className="text-sm text-white/50">No projected tasks in this state.</p>
        ) : (
          <ul className="space-y-3">
            {tasks.map((task) => (
              <li
                key={task.id}
                className="rounded-xl border border-white/10 bg-white/[0.035] p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-semibold text-white">{task.title}</div>
                    <div className="mt-1 text-xs text-white/45">
                      {task.last_updated ? formatDate(task.last_updated) : "not updated"}
                    </div>
                  </div>
                  <Badge tone={taskStatusTone(task.status)}>{task.status}</Badge>
                </div>
                {task.blockers.length > 0 && (
                  <div className="mt-2 text-xs text-coral">{task.blockers[0]}</div>
                )}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </aside>
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

function DetailBlock({
  title,
  children,
}: {
  title: string;
  children: string;
}) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-white/40">
        {title}
      </div>
      <p className="mt-1 leading-relaxed text-white/65">{children}</p>
    </div>
  );
}

function DetailList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wide text-white/40">
        {title}
      </div>
      {items.length === 0 ? (
        <p className="mt-1 text-white/45">None configured.</p>
      ) : (
        <ul className="mt-2 space-y-1">
          {items.map((item) => (
            <li key={item} className="rounded-lg bg-white/[0.04] px-2 py-1 font-mono text-xs text-white/70">
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
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

function taskStatusTone(status: ApiProcessTask["status"]): BadgeTone {
  if (status === "blocked") return "err";
  if (status === "done") return "ok";
  return "info";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

const mockProcess: ApiProcess = {
  id: "development",
  name: "Development Process",
  primary: true,
  state_count: 5,
  task_count: 3,
  blocked_count: 1,
  health: "degraded",
  specialists: [],
  states: [
    mockState("task_intake", "Task Intake", "Intake specialist", "ok", 1, 0),
    mockState("ba_requirements", "BA Requirements", "Business analyst", "ok", 0, 0),
    mockState("dev_implementation", "Dev Implementation", "Developer", "degraded", 1, 1),
    mockState("qa_manual", "QA Manual", "QA engineer", "ok", 0, 0),
    mockState("pr_review", "PR Review", "Code reviewer", "ok", 1, 0),
  ],
  transitions: [],
  tasks: [
    {
      id: "mock-task-1",
      title: "Clarify payment retry requirements",
      state_id: "task_intake",
      status: "active",
      last_updated: new Date().toISOString(),
      context: { source: "mock" },
      blockers: [],
    },
    {
      id: "mock-task-2",
      title: "Checkout flow implementation is waiting for approval",
      state_id: "dev_implementation",
      status: "blocked",
      last_updated: new Date().toISOString(),
      context: { source: "mock" },
      blockers: ["Approval required before touching payment code."],
    },
    {
      id: "mock-task-3",
      title: "Reviewed PR · 2 suggestions",
      state_id: "pr_review",
      status: "done",
      last_updated: new Date().toISOString(),
      context: { source: "mock" },
      blockers: [],
    },
  ],
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
