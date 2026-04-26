"use client";

import { useEffect, useMemo, useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type { ApiProcess, ApiRepoConfig } from "@/lib/api/client";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";

const DEFAULT_TRACKER = "ship";

export function TrackerMappingPanel({
  workspaceId,
  process,
  repoId,
  config,
}: {
  workspaceId: string;
  process: ApiProcess;
  repoId?: string;
  config: ApiRepoConfig | null;
}) {
  const canonicalStates = useMemo(() => collectCanonicalStates(process), [process]);
  const [tracker, setTracker] = useState(DEFAULT_TRACKER);
  const [mapping, setMapping] = useState<Record<string, Record<string, string>>>(() =>
    normalizedMapping(process, canonicalStates),
  );

  useEffect(() => {
    setMapping(normalizedMapping(process, canonicalStates));
  }, [process, canonicalStates]);

  const processDraft = useMemo<ApiProcess>(
    () => ({ ...process, tracker_mapping: mapping }),
    [process, mapping],
  );
  const processConfig = useMemo(
    () => processConfigFromApiProcess(processDraft),
    [processDraft],
  );
  const initialConfig = useMemo(
    () =>
      processConfigFromApiProcess({
        ...process,
        tracker_mapping: normalizedMapping(process, canonicalStates),
      }),
    [process, canonicalStates],
  );
  const initialReviewProcess = useMemo<ApiProcess>(
    () => ({
      ...process,
      tracker_mapping: normalizedMapping(process, canonicalStates),
    }),
    [process, canonicalStates],
  );
  const dirty = JSON.stringify(processConfig) !== JSON.stringify(initialConfig);
  const changeSummary = processChangeSummary(initialReviewProcess, processDraft, [
    ...(dirty ? ["Tracker state mapping changed"] : []),
  ]);
  const activeMapping = mapping[tracker] ?? {};
  const missing = canonicalStates.filter((state) => !activeMapping[state]?.trim());

  function patchCanonical(canonical: string, native: string) {
    setMapping((current) => ({
      ...current,
      [tracker]: {
        ...(current[tracker] ?? {}),
        [canonical]: native,
      },
    }));
  }

  return (
    <Card>
      <div className="space-y-4 p-1">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <CardHeader
            className="p-0"
            title="Tracker mapping"
            subtitle="Map Ship canonical states to tracker statuses or labels before activating the process."
          />
          <form
            action="/api/process/config-propose"
            method="post"
            className="flex flex-wrap items-center gap-2"
          >
            <ProcessConfigProposalFields
              workspaceId={workspaceId}
              repoId={repoId}
              config={config}
              processConfig={processConfig}
              changeSummary={changeSummary}
            />
            <button
              type="submit"
              disabled={!repoId || !dirty || missing.length > 0}
              className="h-9 whitespace-nowrap rounded-full border border-aqua/30 bg-aqua/10 px-4 text-xs font-bold text-aqua transition hover:bg-aqua/15 disabled:cursor-not-allowed disabled:border-white/10 disabled:bg-white/[0.05] disabled:text-white/35"
            >
              Review changes
            </button>
          </form>
        </div>

        <ProcessReviewSummary
          initial={initialReviewProcess}
          draft={processDraft}
          changedAreas={dirty ? ["Tracker state mapping changed"] : []}
        />

        <div className="grid gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
            <label className="block">
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
                Tracker projection
              </span>
              <input
                value={tracker}
                onChange={(e) => {
                  const next = e.target.value.trim() || DEFAULT_TRACKER;
                  setTracker(next);
                  setMapping((current) => ({
                    ...current,
                    [next]: current[next] ?? Object.fromEntries(canonicalStates.map((s) => [s, titleize(s)])),
                  }));
                }}
                className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              />
            </label>
            <p className="mt-2 text-xs leading-relaxed text-white/45">
              Use `ship` for Ship-side FSM only, or a tracker key like `linear`,
              `jira`, `github_issues`, or `notion` when reflecting states outward.
            </p>
          </div>
          <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
            <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
              Reconcile status
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              {process.adapter_diagnostics.map((diag) => (
                <div key={`${diag.kind}-${diag.name}`} className="rounded-lg border border-white/10 bg-white/[0.03] p-2">
                  <div className="text-xs font-semibold text-white/80">{diag.name}</div>
                  <div className="mt-1 text-[11px] text-white/45">{diag.message}</div>
                  <div className="mt-2 text-[10px] uppercase tracking-widest text-white/30">
                    {diag.status}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {missing.length > 0 ? (
          <div className="rounded-xl border border-coral/25 bg-coral/[0.05] px-3 py-2 text-xs text-coral/90">
            Missing mappings: {missing.join(", ")}.
          </div>
        ) : (
          <div className="rounded-xl border border-aqua/20 bg-aqua/[0.05] px-3 py-2 text-xs text-aqua/90">
            All canonical states used by this flow have a tracker projection.
          </div>
        )}

        <div className="overflow-hidden rounded-xl border border-white/10">
          <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] border-b border-white/10 bg-white/[0.035] px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-white/40">
            <div>Ship canonical state</div>
            <div>Tracker status / label</div>
          </div>
          {canonicalStates.map((canonical) => (
            <div
              key={canonical}
              className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] items-center gap-3 border-b border-white/10 px-3 py-2 last:border-b-0"
            >
              <div>
                <div className="font-mono text-xs text-white/75">{canonical}</div>
                <div className="mt-0.5 text-[11px] text-white/35">{usageFor(canonical, process)}</div>
              </div>
              <input
                value={activeMapping[canonical] ?? ""}
                onChange={(e) => patchCanonical(canonical, e.target.value)}
                placeholder={titleize(canonical)}
                className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
              />
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function collectCanonicalStates(process: ApiProcess) {
  const values = new Set<string>();
  for (const state of process.states) {
    const contract = state.ticket_contract;
    if (!contract) continue;
    [
      contract.input_state,
      contract.claim_state,
      contract.success_state,
      contract.blocked_state,
      contract.needs_info_state,
      contract.approval_state,
    ].forEach((value) => {
      if (value) values.add(value);
    });
  }
  return Array.from(values).sort();
}

function normalizedMapping(
  process: ApiProcess,
  canonicalStates: string[],
): Record<string, Record<string, string>> {
  const existing = process.tracker_mapping ?? {};
  if (Object.keys(existing).length > 0) return existing;
  return {
    [DEFAULT_TRACKER]: Object.fromEntries(canonicalStates.map((state) => [state, titleize(state)])),
  };
}

function titleize(value: string) {
  return value.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function usageFor(canonical: string, process: ApiProcess) {
  const states = process.states
    .filter((state) => Object.values(state.ticket_contract ?? {}).includes(canonical))
    .map((state) => state.name);
  return states.length ? `Used by ${states.join(", ")}` : "Not used by current flow";
}
