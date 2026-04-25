"use client";

import { useEffect, useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type {
  ApiProcessState,
  ApiProcessTransition,
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
  states,
  specialistOptions,
  transitions,
  config,
  onStateChange,
  onStateRename,
  onTransitionsChange,
  onAddState,
  onDeleteState,
  embedded = false,
}: {
  repoId?: string;
  state?: ApiProcessState;
  states: ApiProcessState[];
  specialistOptions: SpecialistOption[];
  transitions: ApiProcessTransition[];
  config: ApiRepoConfig | null;
  onStateChange: (state: ApiProcessState) => void;
  onStateRename: (currentId: string, nextId: string) => boolean;
  onTransitionsChange: (transitions: ApiProcessTransition[]) => void;
  onAddState: () => void;
  onDeleteState: (stateId: string) => void;
  embedded?: boolean;
}) {
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
  const exitExpression = selectedState.exit_conditions[0]?.expression ?? "";
  const blockExpression = selectedState.block_conditions[0]?.expression ?? "";
  const exitLabel = humanCondition(
    exitExpression,
    "The owner marks the step complete.",
  );
  const blockLabel = humanCondition(
    blockExpression,
    "The agent needs a decision, approval, or missing context.",
  );

  function patchState(patch: Partial<EditableProcessState>) {
    onStateChange({ ...selectedState, ...patch });
  }

  function patchTrigger(
    patch: Partial<ApiProcessState["triggers"][number]>,
  ) {
    const trigger = selectedState.triggers[0] ?? {
      type: "manual" as const,
      interval: null,
      event: null,
    };
    patchState({ triggers: [{ ...trigger, ...patch }] });
  }

  function patchCondition(
    kind: "exit_conditions" | "block_conditions",
    value: string,
    originalExpression: string,
    originalLabel: string,
  ) {
    const expression =
      value === originalLabel ? originalExpression : value.trim();
    patchState({ [kind]: expression ? [{ expression }] : [] });
  }

  const editorContent = (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <CardHeader
          title="State settings"
          subtitle="Edit the selected step in product language."
        />
        <button
          type="button"
          onClick={onAddState}
          className="rounded-full border border-aqua/25 bg-aqua/10 px-3 py-1 text-xs font-semibold text-aqua hover:bg-aqua/15"
        >
          Add state
        </button>
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
          <EditorField
            label="Step name"
            value={selectedState.name}
            onChange={(value) => patchState({ name: value })}
          />
          <StateKeyField
            stateId={selectedState.id}
            states={states}
            onRename={(nextId) => onStateRename(selectedState.id, nextId)}
          />
          <RoleSelector
            value={selectedState.specialist_id}
            options={specialistOptions}
            onChange={(specialist) =>
              patchState({
                specialist_id: specialist.id,
                specialist_name: specialist.name,
              })
            }
          />
          <EditorField
            label="Role display name"
            value={selectedState.specialist_name}
            onChange={(value) => patchState({ specialist_name: value })}
          />
          <AgentProfileSelector
            value={agentProfileFromState(selectedState)}
            onChange={(value) => patchState({ specialist_agent_profile: value })}
          />
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              What should happen here
            </span>
            <textarea
              value={selectedState.instructions}
              onChange={(event) =>
                patchState({ instructions: event.target.value })
              }
              rows={5}
              className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm leading-relaxed text-white outline-none focus:border-aqua/40"
            />
          </label>
          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
              Starts when
            </div>
            <select
              value={selectedState.triggers[0]?.type ?? "manual"}
              onChange={(event) =>
                patchTrigger({
                  type: event.target
                    .value as ApiProcessState["triggers"][number]["type"],
                })
              }
              className="mt-2 w-full rounded-lg border border-white/10 bg-ink px-2 py-2 text-sm text-white outline-none focus:border-aqua/40"
            >
              <option value="manual">Someone starts it manually</option>
              <option value="event">A connected tool sends an event</option>
              <option value="schedule">It runs on a schedule</option>
            </select>
            <input
              value={humanTriggerDetail(selectedState)}
              onChange={(event) => {
                const type = selectedState.triggers[0]?.type ?? "manual";
                patchTrigger(
                  type === "schedule"
                    ? { interval: event.target.value, event: null }
                    : type === "event"
                      ? { interval: null, event: event.target.value }
                      : { interval: null, event: null },
                );
              }}
              placeholder="Example: every weekday morning, or ticket moved to Ready"
              className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs text-white outline-none focus:border-aqua/40"
            />
          </div>
          <RuleTextArea
            label="Ready to move forward when"
            value={exitLabel}
            onChange={(value) =>
              patchCondition(
                "exit_conditions",
                value,
                exitExpression,
                exitLabel,
              )
            }
          />
          <RuleTextArea
            label="Pause and ask for help when"
            value={blockLabel}
            onChange={(value) =>
              patchCondition(
                "block_conditions",
                value,
                blockExpression,
                blockLabel,
              )
            }
          />
          <TransitionEditor
            selectedStateId={selectedState.id}
            states={states}
            transitions={transitions}
            onChange={onTransitionsChange}
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
              disabled={states.length <= 1}
              onClick={() => onDeleteState(selectedState.id)}
              className="mt-3 rounded-full border border-coral/30 bg-coral/10 px-3 py-1 text-xs font-semibold text-coral hover:bg-coral/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.04] disabled:text-white/35"
            >
              Delete state
            </button>
          </div>
          {config?.raw_yaml && (
            <details className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
              <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-widest text-white/45">
                Source .ship/config.yml
              </summary>
              <pre className="mt-3 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg bg-black/25 p-3 text-[11px] leading-relaxed text-white/55">
                {config.raw_yaml}
              </pre>
            </details>
          )}
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

function StateKeyField({
  stateId,
  states,
  onRename,
}: {
  stateId: string;
  states: ApiProcessState[];
  onRename: (nextId: string) => boolean;
}) {
  const [draft, setDraft] = useState(stateId);
  const normalized = normalizeStateId(draft);
  const duplicate = Boolean(
    normalized &&
      normalized !== stateId &&
      states.some((state) => state.id === normalized),
  );
  const invalid = !normalized;
  const changed = normalized !== stateId;

  useEffect(() => {
    setDraft(stateId);
  }, [stateId]);

  function applyRename() {
    if (!changed || duplicate || invalid) {
      setDraft(stateId);
      return;
    }
    const renamed = onRename(normalized);
    setDraft(renamed ? normalized : stateId);
  }

  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        Step key
      </span>
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={applyRename}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            applyRename();
          }
          if (event.key === "Escape") {
            setDraft(stateId);
            event.currentTarget.blur();
          }
        }}
        className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 font-mono text-sm text-white outline-none focus:border-aqua/40"
      />
      <span
        className={`mt-1 block text-xs ${
          duplicate || invalid ? "text-coral/80" : "text-white/40"
        }`}
      >
        {invalid
          ? "Use at least one letter or number."
          : duplicate
            ? "This key is already used by another state."
            : changed
              ? `Will save as ${normalized}.`
              : "Used in .ship/config.yml transitions."}
      </span>
    </label>
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
        <span className="text-white/40">
          {selectedOption?.role ?? "Choose who owns this state."}
        </span>
        {selectedOption?.source ? (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white/35">
            {sourceLabel(selectedOption.source)}
          </span>
        ) : null}
      </div>
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

function RuleTextArea({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <textarea
        value={value}
        onChange={(event) => onChange(event.target.value)}
        rows={3}
        className="mt-2 w-full resize-none rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs leading-relaxed text-white outline-none focus:border-aqua/40"
      />
    </div>
  );
}

function TransitionEditor({
  selectedStateId,
  states,
  transitions,
  onChange,
}: {
  selectedStateId: string;
  states: ApiProcessState[];
  transitions: ApiProcessTransition[];
  onChange: (transitions: ApiProcessTransition[]) => void;
}) {
  const outgoingTransitions = transitions
    .map((transition, index) => ({ transition, index }))
    .filter(({ transition }) => transition.from_state_id === selectedStateId);
  const availableTargets = states.filter((state) => state.id !== selectedStateId);

  function patchTransition(
    index: number,
    patch: Partial<ApiProcessTransition>,
  ) {
    onChange(
      transitions.map((transition, currentIndex) =>
        currentIndex === index
          ? {
              ...transition,
              ...patch,
              id: transitionId({ ...transition, ...patch }, currentIndex),
            }
          : transition,
      ),
    );
  }

  function addTransition() {
    const firstTarget =
      availableTargets.find(
        (state) =>
          !outgoingTransitions.some(
            ({ transition }) => transition.to_state_id === state.id,
          ),
      )?.id ??
      availableTargets[0]?.id ??
      selectedStateId;
    const next = {
      id: "",
      from_state_id: selectedStateId,
      to_state_id: firstTarget,
      conditions: [{ expression: "exit_conditions_met == true" }],
    };
    onChange([...transitions, { ...next, id: transitionId(next, transitions.length) }]);
  }

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
            Next steps
          </div>
          <div className="mt-1 text-xs text-white/45">
            Choose where this state can move next.
          </div>
        </div>
        <button
          type="button"
          onClick={addTransition}
          disabled={availableTargets.length === 0}
          className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs font-semibold text-white/70 hover:border-white/20 disabled:cursor-not-allowed disabled:text-white/30"
        >
          Add
        </button>
      </div>
      <div className="mt-3 space-y-3">
        {outgoingTransitions.map(({ transition, index }) => (
          <div
            key={`${transition.id}-${index}`}
            className="rounded-xl border border-white/10 bg-black/15 p-2"
          >
            <TransitionSelect
              label="Move to"
              value={transition.to_state_id}
              states={availableTargets}
              onChange={(value) => patchTransition(index, { to_state_id: value })}
            />
            <label className="mt-2 block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/35">
                Rule for this path
              </span>
            <input
              value={transition.conditions[0]?.expression ?? ""}
              onChange={(event) =>
                patchTransition(index, {
                  conditions: event.target.value
                    ? [{ expression: event.target.value }]
                    : [],
                })
              }
              placeholder="Optional expression, e.g. exit_conditions_met == true"
              className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs text-white outline-none focus:border-aqua/40"
            />
            </label>
            <button
              type="button"
              onClick={() =>
                onChange(transitions.filter((_, currentIndex) => currentIndex !== index))
              }
              className="mt-2 text-xs font-semibold text-coral/80 hover:text-coral"
            >
              Remove transition
            </button>
          </div>
        ))}
        {outgoingTransitions.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-black/15 px-3 py-2 text-xs text-white/45">
            No next steps from this state yet.
          </div>
        )}
      </div>
    </div>
  );
}

function TransitionSelect({
  label,
  value,
  states,
  onChange,
}: {
  label: string;
  value: string;
  states: ApiProcessState[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/35">
        {label}
      </span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-lg border border-white/10 bg-ink px-2 py-2 text-xs text-white outline-none focus:border-aqua/40"
      >
        {states.map((state) => (
          <option key={state.id} value={state.id}>
            {state.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function transitionId(
  transition: Pick<ApiProcessTransition, "from_state_id" | "to_state_id">,
  index: number,
) {
  return `${transition.from_state_id}_to_${transition.to_state_id}_${index + 1}`;
}

function normalizeStateId(value: string) {
  return value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .replace(/_+/g, "_");
}

function humanTriggerDetail(state: ApiProcessState): string {
  const trigger = state.triggers[0];
  if (!trigger) return "";
  if (trigger.type === "schedule") return trigger.interval ?? "";
  if (trigger.type === "event") return trigger.event ?? "";
  return "";
}

function humanCondition(value: string | undefined, fallback: string): string {
  if (!value) return fallback;
  if (value === "state_complete == true") {
    return "The owner marks the step complete.";
  }
  if (value === "requires_human_input == true") {
    return "The agent needs a decision, approval, or missing context.";
  }
  return value;
}

function agentProfileFromState(state: ApiProcessState): string {
  const extended = state as EditableProcessState;
  return extended.specialist_agent_profile || "auto";
}
