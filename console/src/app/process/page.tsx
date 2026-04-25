import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, type BadgeTone, ButtonPrimary, Card, CardHeader, MockBanner } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiProcess,
  type ApiProcessAdapterDiagnostic,
  type ApiProcessHealth,
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

  if (!isApiConfigured()) {
    return renderProcessPage({
      workspace: { id: "mock", name: "Mock workspace", slug: "mock" },
      process: mockProcess,
      selectedStateId,
      mock: true,
    });
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fprocess");

  const result = await loadLiveProcess(token);
  if (result === "unauthorized") redirect("/login?next=%2Fprocess");
  if (result === "empty") redirect("/onboarding?step=github");
  if (result === "down") return renderDownState();

  return renderProcessPage({ ...result, selectedStateId });
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
  mock = false,
}: {
  workspace: Pick<ApiWorkspace, "id" | "name" | "slug">;
  process: ApiProcess;
  selectedStateId?: string;
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
          <ButtonPrimary>
            <Link href="/process?mode=edit">Edit process</Link>
          </ButtonPrimary>
        </>
      }
    >
      {mock && <MockBanner />}
      <div className="space-y-4">
        <ProcessHero process={process} />
        <AdapterDiagnostics diagnostics={process.adapter_diagnostics} />
        <ProcessCanvas process={process} selectedStateId={selectedState?.id} />
        <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <StateDetails state={selectedState} tasks={selectedTasks} />
          <RoutinesPanel process={process} />
        </section>
      </div>
    </AppShell>
  );
}

function ProcessHero({ process }: { process: ApiProcess }) {
  return (
    <Card className="relative overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Badge tone={healthTone(process.health)} dot>
            {process.health}
          </Badge>
          <h2 className="mt-3 font-display text-3xl font-bold text-white">
            {process.name}
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-relaxed text-white/60">
            Read-only FSM projection over the current runtime. States own work,
            specialists execute it, and execution history stays behind the
            process abstraction.
          </p>
        </div>
        <div className="grid grid-cols-3 gap-2 text-center">
          <Metric label="States" value={process.state_count} tone="info" />
          <Metric label="Tasks" value={process.task_count} tone="neutral" />
          <Metric label="Blocked" value={process.blocked_count} tone={process.blocked_count > 0 ? "warn" : "ok"} />
        </div>
      </div>
    </Card>
  );
}

function ProcessCanvas({
  process,
  selectedStateId,
}: {
  process: ApiProcess;
  selectedStateId?: string;
}) {
  return (
    <Card>
      <CardHeader
        title="Development FSM"
        subtitle="Responsive process map. States wrap so the full flow stays visible."
      />
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-4">
        {process.states.map((state, index) => (
          <StateNode
            key={state.id}
            state={state}
            selected={state.id === selectedStateId}
            step={index + 1}
            nextName={process.states[index + 1]?.name}
          />
        ))}
      </div>
      {process.states.length > 4 && (
        <p className="mt-3 text-xs text-white/45">
          Large processes wrap into additional rows. Use the state detail panel
          below to inspect rules and current tasks.
        </p>
      )}
    </Card>
  );
}

function StateNode({
  state,
  selected,
  step,
  nextName,
}: {
  state: ApiProcessState;
  selected: boolean;
  step: number;
  nextName?: string;
}) {
  return (
    <Link
      href={`/process?state=${encodeURIComponent(state.id)}`}
      className={[
        "block min-h-[188px] rounded-2xl border p-4 transition",
        selected
          ? "border-aqua/60 bg-aqua/[0.08] shadow-glow"
          : "border-white/10 bg-white/[0.035] hover:border-white/25 hover:bg-white/[0.06]",
      ].join(" ")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-white/35">
            Step {step}
          </div>
          <div className="mt-1 truncate font-display text-base font-bold text-white">
            {state.name}
          </div>
          <div className="mt-1 truncate text-xs text-white/55">
            {state.specialist_name}
          </div>
        </div>
        <Badge tone={healthTone(state.runtime.health)} dot>
          {state.runtime.health}
        </Badge>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2">
        <Metric label="Tasks" value={state.runtime.task_count} tone="neutral" />
        <Metric
          label="Blocked"
          value={state.runtime.blocked_count}
          tone={state.runtime.blocked_count > 0 ? "warn" : "ok"}
        />
      </div>
      <div className="mt-3 text-[11px] text-white/45">
        Last execution:{" "}
        <span className="text-white/65">
          {state.runtime.last_execution_time
            ? formatDate(state.runtime.last_execution_time)
            : "not tracked"}
        </span>
      </div>
      <div className="mt-3 border-t border-white/10 pt-2 text-[11px] text-white/40">
        {nextName ? (
          <>
            Next: <span className="text-white/65">{nextName}</span>
          </>
        ) : (
          <span>Terminal state</span>
        )}
      </div>
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
    <div className="space-y-4">
      <Card>
        <CardHeader
          title={state.name}
          subtitle={`Assigned to ${state.specialist_name}`}
          action={<Badge tone={healthTone(state.runtime.health)}>{state.runtime.health}</Badge>}
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
          title="Tasks in state"
          subtitle="Projected from current execution windows and Inbox blockers."
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
    </div>
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

function AdapterDiagnostics({
  diagnostics,
}: {
  diagnostics: ApiProcessAdapterDiagnostic[];
}) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
      {diagnostics.map((item) => (
        <Card key={`${item.kind}-${item.name}`} className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-xs uppercase tracking-wide text-white/40">
                {item.kind} adapter
              </div>
              <div className="mt-1 font-semibold text-white">{item.name}</div>
            </div>
            <Badge tone={adapterTone(item.status)}>{item.status}</Badge>
          </div>
          <p className="mt-2 text-xs leading-relaxed text-white/55">{item.message}</p>
        </Card>
      ))}
    </div>
  );
}

function Metric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: BadgeTone;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-black/20 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className={`mt-1 font-display text-xl font-bold ${metricTone(tone)}`}>
        {value}
      </div>
    </div>
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

function healthTone(health: ApiProcessHealth): BadgeTone {
  if (health === "failed") return "err";
  if (health === "degraded") return "warn";
  return "ok";
}

function taskStatusTone(status: ApiProcessTask["status"]): BadgeTone {
  if (status === "blocked") return "err";
  if (status === "done") return "ok";
  return "info";
}

function adapterTone(status: ApiProcessAdapterDiagnostic["status"]): BadgeTone {
  if (status === "ok") return "ok";
  if (status === "degraded") return "warn";
  if (status === "not_configured") return "neutral";
  return "info";
}

function metricTone(tone: BadgeTone): string {
  if (tone === "ok") return "text-emerald-300";
  if (tone === "warn") return "text-sun";
  if (tone === "err") return "text-coral";
  if (tone === "info") return "text-sky-300";
  return "text-white";
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
  health: ApiProcessHealth,
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
