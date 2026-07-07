"use client";

import { useEffect, useState, type ReactNode } from "react";

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
  specialist_model?: string | null;
};

// Agent provider → the catalogue provider key sent to the live
// /api/deploy/planner-models picker. anthropic/openai come from models.dev
// (keyless); cursor is served by the backend via a platform Cursor key
// (api.cursor.com/v1/models), with a curated auto/composer fallback when no
// key is set. Any provider NOT listed here falls through to the free-text
// path below.
const CATALOG_PROVIDER: Record<string, string> = {
  claude: "anthropic",
  codex: "openai",
  // Cursor now has a live catalogue too — the backend lists it via a
  // platform Cursor key (api.cursor.com/v1/models). Falls back to a curated
  // list (auto / composer) server-side when no key is configured, so the
  // dropdown is always populated.
  cursor: "cursor",
};

// Per-stage execution-backend profile → the agent provider that actually runs
// the stage. cursor_agent / codex_cli / claude_code pin a provider; the rest
// (main / auto / cheaper / ship_cloud_agent / local_cli) defer to the workspace
// default (agent_provider). This mapping is shared by two consumers: the Model
// picker (so a stage set to "Codex CLI" shows OpenAI models, not the
// workspace's Cursor/Composer) AND — via the CLI's own copy in routines.mjs —
// the runtime, which now honours the same override when launching the agent.
const PROFILE_PROVIDER: Record<string, string> = {
  cursor_agent: "cursor",
  codex_cli: "codex",
  claude_code: "claude",
};

function providerForProfile(
  profile: string,
  workspaceProvider?: string,
): string | undefined {
  return PROFILE_PROVIDER[profile] ?? workspaceProvider;
}

// Cursor exposes no keyless model catalogue and its slugs vary by plan, so we
// offer suggestions but keep the field free-text (whatever you type goes to
// `cursor-agent --model`). ``auto`` is always valid (incl. Free plans);
// ``composer`` is Cursor's rolling alias for the latest Composer (so operators
// needn't track version bumps like composer-2 → composer-2.5).
const CURSOR_MODEL_SUGGESTIONS = ["auto", "composer", "composer-latest"];

export function StateEditor({
  processId,
  workspaceId,
  agentProvider,
  repoId,
  state,
  states,
  schedule,
  transitions,
  specialistOptions,
  config,
  onStateChange,
  onDeleteState,
  onClose,
  embedded = false,
}: {
  processId?: string;
  workspaceId?: string;
  agentProvider?: string;
  repoId?: string;
  state?: ApiProcessState;
  states: ApiProcessState[];
  schedule?: ApiProcess["schedule"] | null;
  transitions: ApiProcessTransition[];
  specialistOptions: SpecialistOption[];
  config: ApiRepoConfig | null;
  onStateChange: (state: ApiProcessState) => void;
  onDeleteState: (stateId: string) => void;
  /** Optional dismiss handler — when wired, renders an X in the header
   *  so the operator can stash the inspector away. */
  onClose?: () => void;
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
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-aqua/70">
          Stage settings
        </div>
        <div className="flex items-center gap-1.5">
          <span
            className="rounded-md border border-white/10 bg-black/30 px-2 py-0.5 text-[10px] font-semibold uppercase text-white/55"
            title={`Lifecycle bucket: ${selectedState.state}`}
          >
            {selectedState.state}
          </span>
          {onClose ? (
            <button
              type="button"
              onClick={onClose}
              title="Close inspector (or click the stage card again)"
              aria-label="Close inspector"
              className="grid h-5 w-5 place-items-center rounded-md border border-white/10 bg-white/[0.02] text-[12px] font-bold text-white/45 transition hover:border-white/25 hover:bg-white/[0.06] hover:text-white"
            >
              ×
            </button>
          ) : null}
        </div>
      </div>

      {config?.parse_error && (
        <div className="rounded-md border border-coral/25 bg-coral/[0.05] px-2 py-1.5 text-[11px] text-coral/90">
          Config YAML parse error: {config.parse_error}
        </div>
      )}
      {!repoId && (
        <div className="rounded-md border border-sun/25 bg-sun/[0.06] px-2 py-1.5 text-[11px] text-sun/90">
          Select a repository before saving.
        </div>
      )}

      {/* IDENTITY — name, role, instructions. The single most-edited
          block; always open. The old 6-section panel had a verbose
          documentation paragraph between fields ("uses the managed
          role template, injected ticket context, …") which was
          docs-grade copy in a UI surface; deleted. */}
      <div className="space-y-2.5 rounded-xl border border-white/10 bg-white/[0.03] p-3">
        <EditorField
          label="Stage name"
          value={selectedState.name}
          onChange={(value) => patchState({ name: value })}
        />
        <RoleSelector
          value={selectedState.specialist_id}
          options={specialistOptions}
          onChange={(specialist) =>
            // DON'T copy specialist.role into instructions — that auto-fill
            // made "Additional guidance" look like a duplicate of the
            // role template instruction. Additional guidance is for
            // operator-typed overrides only; empty by default.
            patchState({
              specialist_id: specialist.id,
              specialist_name: specialist.name,
            })
          }
        />
        <label className="block">
          <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
            Additional guidance{" "}
            <span className="font-normal normal-case tracking-normal text-white/30">
              · optional
            </span>
          </span>
          <textarea
            value={selectedState.instructions}
            onChange={(event) => patchState({ instructions: event.target.value })}
            rows={3}
            placeholder="Override or extend the role template instructions for this stage. Leave blank to use the template as-is."
            className="w-full resize-none rounded-md border border-white/10 bg-black/25 px-2 py-1.5 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-aqua/40 focus:bg-aqua/[0.04]"
          />
        </label>
      </div>

      {/* BEHAVIOUR — schedule + next handoff + advanced agent profile.
          Collapsed by default. Operators rarely touch these per-stage. */}
      {/* BEHAVIOUR — capacity slot link + agent backend dropdown.
          NextHandoff dropped (the next stage is already visible at the
          bottom of the stage card on the canvas). Operators rarely
          touch these per-stage; collapsed by default. */}
      <details className="rounded-xl border border-white/10 bg-white/[0.025] p-3 [&[open]>summary]:mb-3">
        <summary className="cursor-pointer text-[10px] font-bold uppercase tracking-widest text-white/55">
          Behaviour
        </summary>
        <div className="space-y-3 text-xs">
          <div>
            <div className="mb-1 text-[10px] font-bold uppercase tracking-widest text-white/45">
              Capacity slot
            </div>
            <ScheduleSummary
              state={selectedState}
              schedule={schedule}
              processId={processId}
              repoId={repoId}
            />
          </div>
          <AgentProfileSelector
            value={agentProfileFromState(selectedState)}
            workspaceProvider={agentProvider}
            onChange={(value) => {
              // Switching the execution backend can change the provider
              // (e.g. Cursor → Codex). A model picked for the old provider
              // (composer-2.5) is invalid for the new one, so reset it to
              // "Provider default" when the provider actually changes —
              // otherwise the picker would keep showing a cross-provider
              // model that the runtime can't run.
              const prevProvider = providerForProfile(
                agentProfileFromState(selectedState),
                agentProvider,
              );
              const nextProvider = providerForProfile(value, agentProvider);
              patchState(
                nextProvider !== prevProvider
                  ? { specialist_agent_profile: value, specialist_model: null }
                  : { specialist_agent_profile: value },
              );
            }}
          />
          <ModelSelector
            workspaceId={workspaceId}
            repoId={repoId}
            agentProvider={providerForProfile(
              agentProfileFromState(selectedState),
              agentProvider,
            )}
            value={modelFromState(selectedState)}
            onChange={(value) => patchState({ specialist_model: value })}
          />
        </div>
      </details>

      {/* DELETE — single inline button, no separate "danger zone"
          banner. Disabled when this is the last stage. */}
      <button
        type="button"
        disabled={states.length <= 1}
        onClick={() => {
          const ok = window.confirm(
            `Remove "${selectedState.name}" from the flow? This also removes handoffs connected to it.`,
          );
          if (ok) onDeleteState(selectedState.id);
        }}
        className="w-full rounded-md border border-coral/25 bg-coral/[0.04] px-3 py-1.5 text-[11px] font-semibold text-coral/85 transition hover:bg-coral/[0.10] disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.03] disabled:text-white/30"
      >
        Delete this stage
      </button>
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
    <section className="space-y-3 p-1">
      <div className="text-[10px] font-bold uppercase tracking-widest text-aqua/55">
        {title}
      </div>
      {children}
    </section>
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
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-white/45">
      {matchingSlots.length > 0 ? (
        <span>
          {matchingSlots
            .map((slot) => `${slot.local_time} ${slot.label ?? slot.id}`)
            .join(", ")}
        </span>
      ) : (
        <span className="text-white/35">No capacity slot</span>
      )}
      <a href={scheduleHref} className="font-semibold text-aqua hover:underline">
        Edit
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
  // Role-display + role-source + template-instruction docstrings used
  // to live below the dropdown — they're either redundant (display
  // name is the same as the option label) or read-only documentation
  // (the template instruction). Dropped in the O cleanup; the
  // dropdown alone is enough to set the role.
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
        className="w-full rounded-md border border-white/10 bg-black/35 px-2 py-1.5 text-sm text-white outline-none transition focus:border-aqua/40"
      >
        {options.map((option) => (
          <option key={option.id} value={option.id}>
            {option.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function sourceLabel(source: NonNullable<SpecialistOption["source"]>) {
  if (source === "catalog") return "Base catalog";
  if (source === "process") return "Process";
  return "Custom";
}

// Workspace agent_provider → a human label for the "Workspace default"
// option, so the operator sees which concrete CLI the default resolves to
// (e.g. "Workspace default · Cursor") instead of having to remember it.
const PROVIDER_LABEL: Record<string, string> = {
  cursor: "Cursor",
  codex: "Codex",
  claude: "Claude",
};

function AgentProfileSelector({
  value,
  onChange,
  workspaceProvider,
}: {
  value: string;
  onChange: (value: string) => void;
  workspaceProvider?: string;
}) {
  const selectedOption =
    AGENT_PROFILE_OPTIONS.find((option) => option.id === value) ??
    AGENT_PROFILE_OPTIONS[0];
  // Spell out the workspace default's concrete provider on the "main" option.
  const labelFor = (option: (typeof AGENT_PROFILE_OPTIONS)[number]) =>
    option.id === "main" && workspaceProvider
      ? `${option.name} · ${PROVIDER_LABEL[workspaceProvider] ?? workspaceProvider}`
      : option.name;
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        Execution backend
      </span>
      <select
        value={selectedOption.id}
        onChange={(event) => onChange(event.target.value)}
        title={selectedOption.description}
        className="w-full rounded-md border border-white/10 bg-black/35 px-2 py-1.5 text-sm text-white outline-none transition focus:border-aqua/40"
      >
        {AGENT_PROFILE_OPTIONS.map((option) => (
          <option key={option.id} value={option.id}>
            {labelFor(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function ModelSelector({
  workspaceId,
  repoId,
  agentProvider,
  value,
  onChange,
}: {
  workspaceId?: string;
  repoId?: string;
  agentProvider?: string;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  // Catalogued providers (anthropic / openai / cursor) get the live dropdown;
  // any uncatalogued provider falls back to a free-text field — see
  // CATALOG_PROVIDER / CURSOR_MODEL_SUGGESTIONS.
  const catalogProvider = CATALOG_PROVIDER[agentProvider ?? ""] ?? null;
  const label = (
    <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
      Model{" "}
      <span className="font-normal normal-case tracking-normal text-white/30">
        · {agentProvider ?? "provider"}
      </span>
    </span>
  );

  if (!catalogProvider) {
    // Free-text: Cursor (and any uncatalogued provider). Whatever is typed is
    // passed to the agent CLI's --model; empty → provider default. Cursor has
    // no keyless model catalogue, so we link the operator to Cursor's own
    // up-to-date list to copy a slug from rather than risk a stale dropdown.
    return (
      <div>
        <label className="block">
          {label}
          <input
            list="cursor-model-suggestions"
            value={value ?? ""}
            onChange={(event) => onChange(event.target.value.trim() || null)}
            placeholder="Provider default (e.g. auto, composer)"
            className="w-full rounded-md border border-white/10 bg-black/35 px-2 py-1.5 text-sm text-white outline-none transition placeholder:text-white/30 focus:border-aqua/40"
          />
        </label>
        <datalist id="cursor-model-suggestions">
          {CURSOR_MODEL_SUGGESTIONS.map((model) => (
            <option key={model} value={model} />
          ))}
        </datalist>
        <a
          href="https://cursor.com/help/models-and-usage/available-models"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-block text-[10px] text-white/40 transition hover:text-aqua hover:underline"
        >
          See Cursor&apos;s current models ↗
        </a>
      </div>
    );
  }

  return (
    <label className="block">
      {label}
      <CatalogModelSelect
        workspaceId={workspaceId}
        repoId={repoId}
        provider={catalogProvider}
        value={value}
        onChange={onChange}
      />
    </label>
  );
}

function CatalogModelSelect({
  workspaceId,
  repoId,
  provider,
  value,
  onChange,
}: {
  workspaceId?: string;
  repoId?: string;
  provider: string;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!workspaceId || !repoId) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch("/api/deploy/planner-models", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ws: workspaceId, repoId, provider }),
    })
      .then((res) =>
        res.ok ? res.json() : Promise.reject(new Error(String(res.status))),
      )
      .then((data: { models?: string[] }) => {
        if (!cancelled) setModels(Array.isArray(data.models) ? data.models : []);
      })
      .catch(() => {
        if (!cancelled) {
          setModels([]);
          setError("Couldn't load models — provider default will be used.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, repoId, provider]);

  const current = value ?? "";
  // Keep a previously-saved model selectable even if it's not in the live list.
  const options = current && !models.includes(current) ? [current, ...models] : models;

  return (
    <>
      <select
        value={current}
        onChange={(event) => onChange(event.target.value || null)}
        disabled={!repoId}
        className="w-full rounded-md border border-white/10 bg-black/35 px-2 py-1.5 text-sm text-white outline-none transition focus:border-aqua/40 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">{loading ? "Loading models…" : "Provider default"}</option>
        {options.map((model) => (
          <option key={model} value={model}>
            {model}
          </option>
        ))}
      </select>
      {error ? (
        <span className="mt-1 block text-[10px] text-coral/80">{error}</span>
      ) : null}
    </>
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

function modelFromState(state: ApiProcessState): string | null {
  const extended = state as EditableProcessState;
  const value = extended.specialist_model;
  return value && value.trim() ? value : null;
}
