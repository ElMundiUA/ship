"use client";

import { Card, CardHeader } from "@/components/ui";
import type {
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
  repoId,
  state,
  states: _states,
  specialistOptions,
  config,
  onStateChange,
  onDeleteState,
  embedded = false,
}: {
  repoId?: string;
  state?: ApiProcessState;
  states: ApiProcessState[];
  specialistOptions: SpecialistOption[];
  config: ApiRepoConfig | null;
  onStateChange: (state: ApiProcessState) => void;
  onDeleteState: (stateId: string) => void;
  embedded?: boolean;
}) {
  void _states;
  if (!state) {
    if (embedded) {
      return (
        <aside className="border-t border-white/10 p-4 xl:border-l xl:border-t-0">
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

  function patchState(patch: Partial<EditableProcessState>) {
    onStateChange({ ...selectedState, ...patch });
  }

  const editorContent = (
    <div className="space-y-4">
      <CardHeader
        title="State settings"
        subtitle="Name, role template, and how this step is executed."
      />
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
        <AgentProfileSelector
          value={agentProfileFromState(selectedState)}
          onChange={(value) => patchState({ specialist_agent_profile: value })}
        />
        <div className="rounded-xl border border-coral/20 bg-coral/[0.04] p-3">
          <div className="text-[10px] font-bold uppercase tracking-widest text-coral/80">
            Danger zone
          </div>
          <p className="mt-1 text-xs text-white/45">
            Removing a state also removes transitions connected to it.
          </p>
          <button
            type="button"
            disabled={_states.length <= 1}
            onClick={() => onDeleteState(selectedState.id)}
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
      <aside className="min-h-0 border-t border-white/10 p-4 xl:max-h-[calc(100vh-14rem)] xl:overflow-y-auto xl:border-l xl:border-t-0">
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
        className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
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
        className="w-full rounded-xl border border-white/10 bg-ink px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
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
        className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
      />
    </label>
  );
}

function agentProfileFromState(state: ApiProcessState): string {
  const extended = state as EditableProcessState;
  return extended.specialist_agent_profile || "main";
}
