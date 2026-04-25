import { Card, CardHeader } from "@/components/ui";
import type { ApiProcessState, ApiRepoConfig } from "@/lib/api/client";

export function StateEditor({
  workspaceId,
  repoId,
  state,
  config,
  processConfig,
}: {
  workspaceId: string;
  repoId?: string;
  state?: ApiProcessState;
  config: ApiRepoConfig | null;
  processConfig: Record<string, unknown>;
}) {
  if (!state) {
    return (
      <Card>
        <CardHeader title="State details" subtitle="No state selected." />
      </Card>
    );
  }

  return (
    <aside className="min-h-0 xl:sticky xl:top-28 xl:max-h-[calc(100vh-8rem)] xl:overflow-y-auto">
      <Card className="space-y-4">
        <CardHeader
          title="State settings"
          subtitle="Edit the selected step in product language."
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
        <form action="/api/process/config-propose" method="post" className="space-y-4">
          <input type="hidden" name="workspaceId" value={workspaceId} />
          <input type="hidden" name="repoId" value={repoId ?? ""} />
          <input type="hidden" name="stateId" value={state.id} />
          <input type="hidden" name="baseSha" value={config?.sha ?? ""} />
          <input
            type="hidden"
            name="lanesJson"
            value={JSON.stringify(config?.parsed?.lanes ?? {})}
          />
          <input
            type="hidden"
            name="processJson"
            value={JSON.stringify(processConfig)}
          />
          <EditorField name="stateName" label="Step name" defaultValue={state.name} />
          <EditorField
            name="specialistName"
            label="Owner role"
            defaultValue={state.specialist_name}
          />
          <label className="block">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              What should happen here
            </span>
            <textarea
              name="instructions"
              defaultValue={state.instructions}
              rows={5}
              className="w-full resize-none rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm leading-relaxed text-white outline-none focus:border-aqua/40"
            />
          </label>
          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
              Starts when
            </div>
            <select
              name="triggerType"
              defaultValue={state.triggers[0]?.type ?? "manual"}
              className="mt-2 w-full rounded-lg border border-white/10 bg-ink px-2 py-2 text-sm text-white outline-none focus:border-aqua/40"
            >
              <option value="manual">Someone starts it manually</option>
              <option value="event">A connected tool sends an event</option>
              <option value="schedule">It runs on a schedule</option>
            </select>
            <input
              name="triggerDetail"
              defaultValue={humanTriggerDetail(state)}
              placeholder="Example: every weekday morning, or ticket moved to Ready"
              className="mt-2 w-full rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs text-white outline-none focus:border-aqua/40"
            />
          </div>
          <RuleTextArea
            name="exitCondition"
            label="Ready to move forward when"
            value={humanCondition(
              state.exit_conditions[0]?.expression,
              "The owner marks the step complete.",
            )}
          />
          <RuleTextArea
            name="blockCondition"
            label="Pause and ask for help when"
            value={humanCondition(
              state.block_conditions[0]?.expression,
              "The agent needs a decision, approval, or missing context.",
            )}
          />
          <button
            type="submit"
            disabled={!repoId}
            className="w-full rounded-full border border-aqua/30 bg-aqua/10 px-3 py-2 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
          >
            Open config PR
          </button>
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
        </form>
      </Card>
    </aside>
  );
}

function EditorField({
  name,
  label,
  defaultValue,
}: {
  name: string;
  label: string;
  defaultValue: string;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </span>
      <input
        name={name}
        defaultValue={defaultValue}
        className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
      />
    </label>
  );
}

function RuleTextArea({
  name,
  label,
  value,
}: {
  name: string;
  label: string;
  value: string;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <textarea
        name={name}
        defaultValue={value}
        rows={3}
        className="mt-2 w-full resize-none rounded-lg border border-white/10 bg-white/[0.04] px-2 py-2 text-xs leading-relaxed text-white outline-none focus:border-aqua/40"
      />
    </div>
  );
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
