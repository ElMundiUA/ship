"use client";

import { Card, CardHeader } from "@/components/ui";
import type {
  ApiProcessState,
  ApiProcessTransition,
  ApiRepoConfig,
} from "@/lib/api/client";

export function StateEditor({
  repoId,
  state,
  states,
  transitions,
  config,
  onStateChange,
  onTransitionsChange,
  onAddState,
  onDeleteState,
}: {
  repoId?: string;
  state?: ApiProcessState;
  states: ApiProcessState[];
  transitions: ApiProcessTransition[];
  config: ApiRepoConfig | null;
  onStateChange: (state: ApiProcessState) => void;
  onTransitionsChange: (transitions: ApiProcessTransition[]) => void;
  onAddState: () => void;
  onDeleteState: (stateId: string) => void;
}) {
  if (!state) {
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

  function patchState(patch: Partial<ApiProcessState>) {
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

  return (
    <aside className="min-h-0 xl:sticky xl:top-28 xl:max-h-[calc(100vh-8rem)] xl:overflow-y-auto">
      <Card className="space-y-4">
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
          <EditorField
            label="Owner role"
            value={selectedState.specialist_name}
            onChange={(value) => patchState({ specialist_name: value })}
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
      </Card>
    </aside>
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
      states.find((state) => state.id !== selectedStateId)?.id ?? selectedStateId;
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
            Transitions
          </div>
          <div className="mt-1 text-xs text-white/45">
            Define how states connect in this process.
          </div>
        </div>
        <button
          type="button"
          onClick={addTransition}
          disabled={states.length < 2}
          className="rounded-full border border-white/10 bg-white/[0.05] px-3 py-1 text-xs font-semibold text-white/70 hover:border-white/20 disabled:cursor-not-allowed disabled:text-white/30"
        >
          Add
        </button>
      </div>
      <div className="mt-3 space-y-3">
        {transitions.map((transition, index) => (
          <div
            key={`${transition.id}-${index}`}
            className="rounded-xl border border-white/10 bg-black/15 p-2"
          >
            <div className="grid grid-cols-2 gap-2">
              <TransitionSelect
                label="From"
                value={transition.from_state_id}
                states={states}
                onChange={(value) =>
                  patchTransition(index, { from_state_id: value })
                }
              />
              <TransitionSelect
                label="To"
                value={transition.to_state_id}
                states={states}
                onChange={(value) =>
                  patchTransition(index, { to_state_id: value })
                }
              />
            </div>
            <input
              value={transition.conditions[0]?.expression ?? ""}
              onChange={(event) =>
                patchTransition(index, {
                  conditions: event.target.value
                    ? [{ expression: event.target.value }]
                    : [],
                })
              }
              placeholder="Condition, e.g. exit_conditions_met == true"
              className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs text-white outline-none focus:border-aqua/40"
            />
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
        {transitions.length === 0 && (
          <div className="rounded-xl border border-white/10 bg-black/15 px-3 py-2 text-xs text-white/45">
            No transitions yet.
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
