"use client";

import type { ReactNode } from "react";

import { Card, CardHeader } from "@/components/ui";
import type {
  ApiProcess,
  ApiProcessTransition,
  ApiProcessState,
  ApiRepoConfig,
} from "@/lib/api/client";
import { AGENT_PROFILE_OPTIONS } from "./agent-profile-catalog";

export type SpecialistOption = {
  id: string;
  name: string;
  role: string;
  source?: "catalog" | "process" | "custom";
};

type EditableProcessState = ApiProcessState & {
  specialist_agent_profile?: string | null;
};

export function StateEditor({
  processId,
  repoId,
  state,
  states,
  schedule,
  transitions,
  specialistOptions,
  config,
  onStateChange,
  onDeleteState,
  embedded = false,
}: {
  processId?: string;
  repoId?: string;
  state?: ApiProcessState;
  states: ApiProcessState[];
  schedule?: ApiProcess["schedule"] | null;
  transitions: ApiProcessTransition[];
  specialistOptions: SpecialistOption[];
  config: ApiRepoConfig | null;
  onStateChange: (state: ApiProcessState) => void;
  onDeleteState: (stateId: string) => void;
  embedded?: boolean;
}) {
  if (!state) {
    if (embedded) {
      return (
        <aside className="border-t border-white/10 bg-black/20 p-4 xl:border-l xl:border-t-0">
          <CardHeader title="State details" subtitle="No state selected." />
        </aside>
      );
    }

    return (
      <Card>
        <CardHeader title="State details" subtitle="No state selected." />
      </Card>
    );
  }
  const selectedState = state;
  const selectedRole = specialistOptions.find(
    (option) => option.id === selectedState.specialist_id,
  );
  const nextStates = transitions
    .filter((transition) => transition.from_state_id === selectedState.id)
    .map((transition) => ({
      transition,
      state: states.find((row) => row.id === transition.to_state_id),
    }));

  function patchState(patch: Partial<EditableProcessState>) {
    onStateChange({ ...selectedState, ...patch });
  }

  const editorContent = (
    <div className="space-y-4">
      <div className="rounded-3xl border border-white/10 bg-[radial-gradient(circle_at_top_left,rgba(99,245,255,0.12),transparent_32%),linear-gradient(135deg,rgba(255,255,255,0.08),rgba(255,255,255,0.03))] p-4">
        <div className="text-[10px] font-bold uppercase tracking-[0.24em] text-aqua/70">
          Inspector
        </div>
        <h2 className="mt-2 font-display text-xl font-bold text-white">
          Step settings
        </h2>
        <p className="mt-1 text-xs leading-relaxed text-white/50">
          Tune what happens, how work is picked, when it can run, and where it goes next.
        </p>
      </div>
      {config?.parse_error && (
        <div className="rounded-xl border border-coral/25 bg-coral/[0.05] px-3 py-2 text-xs text-coral/90">
          Config YAML parse error: {config.parse_error}
        </div>
      )}
      {!repoId && (
        <div className="rounded-xl border border-amber-300/25 bg-amber-300/[0.06] px-3 py-2 text-xs text-amber-100/90">
          Select a repository before saving process changes.
        </div>
      )}
      <div className="space-y-4">
        <Section title="What happens here?">
          <EditorField
            label="Step name"
            value={selectedState.name}
            onChange={(value) => patchState({ name: value })}
          />
          <RoleSelector
            value={selectedState.specialist_id}
            options={specialistOptions}
            onChange={(specialist) =>
              patchState({
                specialist_id: specialist.id,
                specialist_name: specialist.name,
                instructions: specialist.role,
              })
            }
          />
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Additional guidance
            </span>
            <textarea
              value={selectedState.instructions}
              onChange={(event) => patchState({ instructions: event.target.value })}
              rows={3}
              className="w-full resize-none rounded-2xl border border-white/10 bg-black/25 px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/40 focus:bg-aqua/[0.04]"
            />
          </label>
          <p className="text-xs leading-relaxed text-white/45">
            {selectedRole?.name ?? selectedState.specialist_name} uses the managed
            role template, injected ticket context, workspace policies, and this
            additive guidance. The base role prompt is not replaced here.
          </p>
        </Section>

        <Section title="How work is picked">
          <TicketContractSummary state={selectedState} />
        </Section>

        <Section title="When it can run">
          <ScheduleSummary
            state={selectedState}
            schedule={schedule}
            processId={processId}
            repoId={repoId}
          />
        </Section>

        <Section title="What happens next">
          <NextHandoffSummary nextStates={nextStates} />
        </Section>

        <details className="rounded-2xl border border-white/10 bg-white/[0.035] p-3">
          <summary className="cursor-pointer text-xs font-bold uppercase tracking-widest text-white/45">
            Advanced execution
          </summary>
          <div className="mt-3">
            <AgentProfileSelector
              value={agentProfileFromState(selectedState)}
              onChange={(value) => patchState({ specialist_agent_profile: value })}
            />
          </div>
        </details>
        <div className="rounded-2xl border border-coral/20 bg-coral/[0.04] p-3">
          <div className="text-[10px] font-bold uppercase tracking-widest text-coral/80">
            Danger zone
          </div>
          <p className="mt-1 text-xs text-white/45">
            Removing a state also removes transitions connected to it.
          </p>
          <button
            type="button"
            disabled={states.length <= 1}
            onClick={() => {
              const ok = window.confirm(
                `Remove "${selectedState.name}" from the flow? This also removes handoffs connected to it.`,
              );
              if (ok) onDeleteState(selectedState.id);
            }}
            className="mt-3 rounded-full border border-coral/30 bg-coral/10 px-3 py-1 text-xs font-semibold text-coral hover:bg-coral/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-white/35"
          >
            Delete state
          </button>
        </div>
      </div>
    </div>
  );

  if (embedded) {
    return (
      <aside className="min-h-0 border-t border-white/10 bg-black/20 p-4 xl:max-h-[calc(100vh-14rem)] xl:overflow-y-auto xl:border-l xl:border-t-0">
        {editorContent}
      </aside>
    );
  }

  return (
    <aside className="min-h-0 xl:sticky xl:top-28 xl:max-h-[calc(100vh-8rem)] xl:overflow-y-auto">
      <Card>{editorContent}</Card>
    </aside>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3 rounded-2xl border border-white/10 bg-[linear-gradient(135deg,rgba(255,255,255,0.065),rgba(255,255,255,0.025))] p-3 shadow-lg shadow-black/10">
      <div className="text-[10px] font-bold uppercase tracking-widest text-aqua/55">
        {title}
      </div>
      {children}
    </section>
  );
}

function TicketContractSummary({ state }: { state: ApiProcessState }) {
  const contract = state.ticket_contract;
  if (!contract) {
    return (
      <p className="text-xs leading-relaxed text-white/45">
        This step has no tracker-picking contract yet. Ship will not start a
        ticket-driven backend agent until a canonical FSM contract is configured.
      </p>
    );
  }
  return (
    <div className="space-y-2 text-xs text-white/55">
      <p>
        Ship picks a ticket from <CodeLabel>{contract.input_state}</CodeLabel>, claims
        it into <CodeLabel>{contract.claim_state}</CodeLabel>, and injects that ticket
        into the specialist prompt. If no ticket matches, this step does not start.
      </p>
      <div className="grid gap-2">
        <ContractRow label="On success" value={contract.success_state} />
        <ContractRow label="Blocked" value={contract.blocked_state} />
        <ContractRow label="Needs info" value={contract.needs_info_state} />
        <ContractRow label="Human approval" value={contract.approval_state} />
      </div>
    </div>
  );
}

function ContractRow({
  label,
  value,
}: {
  label: string;
  value?: string | null;
}) {
  return (
    <div className="flex items-center justify-between gap-2 rounded-xl bg-black/20 px-2 py-1.5">
      <span className="text-white/40">{label}</span>
      <CodeLabel>{value ?? "not configured"}</CodeLabel>
    </div>
  );
}

function ScheduleSummary({
  state,
  schedule,
  processId,
  repoId,
}: {
  state: ApiProcessState;
  schedule?: ApiProcess["schedule"] | null;
  processId?: string;
  repoId?: string;
}) {
  const matchingSlots =
    schedule?.slots.filter((slot) => slot.specialist_ids.includes(state.specialist_id)) ??
    [];
  const query = new URLSearchParams({ tab: "schedule" });
  if (repoId) query.set("repo", repoId);
  const scheduleHref = processId
    ? `/process/${encodeURIComponent(processId)}?${query.toString()}`
    : `/process?${query.toString()}`;
  if (schedule?.trigger?.kind === "event") {
    return (
      <p className="text-xs leading-relaxed text-white/45">
        Runs when {schedule.trigger.event ?? "the configured event"} matches; no cron
        capacity is required for this trigger.
      </p>
    );
  }
  return (
    <div className="space-y-2 text-xs text-white/50">
      <p>
        {matchingSlots.length
          ? `Can run in ${matchingSlots.map((slot) => `${slot.local_time} ${slot.label ?? slot.id}`).join(", ")} if matching tickets exist.`
          : "This specialist is not included in any flow schedule slot yet."}
      </p>
      <a href={scheduleHref} className="font-semibold text-aqua hover:underline">
        Edit flow schedule
      </a>
    </div>
  );
}

function NextHandoffSummary({
  nextStates,
}: {
  nextStates: { transition: ApiProcessTransition; state?: ApiProcessState }[];
}) {
  if (nextStates.length === 0) {
    return (
      <p className="text-xs leading-relaxed text-white/45">
        No outgoing handoff is configured. On success this may become a terminal step.
      </p>
    );
  }
  return (
    <ul className="space-y-2 text-xs text-white/55">
      {nextStates.map(({ transition, state }) => (
        <li key={transition.id} className="rounded-lg bg-white/[0.03] px-2 py-1">
          Pass to <span className="text-white/80">{state?.name ?? transition.to_state_id}</span>
          {transition.requires_human ? " after human approval" : ""}.
          {transition.conditions[0]?.expression ? (
            <span className="ml-1 text-white/35">
              Condition: {transition.conditions[0].expression}
            </span>
          ) : null}
        </li>
      ))}
      <li className="text-white/35">
        Blocked, needs-info, and ask-human outcomes are controlled by the canonical
        FSM contract above.
      </li>
    </ul>
  );
}

function CodeLabel({ children }: { children: ReactNode }) {
  return <code className="font-mono text-white/75">{children}</code>;
}

function RoleSelector({
  value,
  options,
  onChange,
}: {
  value: string;
  options: SpecialistOption[];
  onChange: (specialist: SpecialistOption) => void;
}) {
  const selectedOption = options.find((option) => option.id === value);
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        Role template
      </span>
      <select
        value={value}
        onChange={(event) => {
          const next = options.find((option) => option.id === event.target.value);
          if (next) onChange(next);
        }}
        className="w-full rounded-2xl border border-white/10 bg-black/35 px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/40"
      >
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
        <span className="text-white/50">
          Role display name:{" "}
          <span className="text-white/80">
            {selectedOption?.name ?? "—"}
          </span>
        </span>
        {selectedOption?.source ? (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/35">
            {sourceLabel(selectedOption.source)}
          </span>
        ) : null}
      </div>
      {selectedOption?.role ? (
        <p className="mt-2 text-xs leading-relaxed text-white/50">
          Specialist instruction (from template): {selectedOption.role}
        </p>
      ) : null}
    </label>
  );
}

function sourceLabel(source: NonNullable<SpecialistOption["source"]>) {
  if (source === "catalog") return "Base catalog";
  if (source === "process") return "Process";
  return "Custom";
}

function AgentProfileSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const selectedOption =
    AGENT_PROFILE_OPTIONS.find((option) => option.id === value) ??
    AGENT_PROFILE_OPTIONS[0];
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        Execution backend
      </span>
      <select
        value={selectedOption.id}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-2xl border border-white/10 bg-black/35 px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/40"
      >
        {AGENT_PROFILE_OPTIONS.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
      <span className="mt-1 block text-xs text-white/40">
        {selectedOption.description}
      </span>
    </label>
  );
}

function EditorField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-2xl border border-white/10 bg-black/25 px-3 py-2 text-sm text-white outline-none transition focus:border-aqua/40 focus:bg-aqua/[0.04]"
      />
    </label>
  );
}

function agentProfileFromState(state: ApiProcessState): string {
  const extended = state as EditableProcessState;
  return extended.specialist_agent_profile || "main";
}
