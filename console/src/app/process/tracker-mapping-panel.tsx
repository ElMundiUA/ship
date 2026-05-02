"use client";

import { useEffect, useId, useMemo, useState } from "react";

import { Card, CardHeader } from "@/components/ui";
import type { ApiProcess, ApiRepoConfig } from "@/lib/api/client";
import { processConfigFromApiProcess } from "./process-config";
import { ProcessConfigProposalFields } from "./process-config-proposal-fields";
import { ProcessReviewSummary, processChangeSummary } from "./process-review-summary";

const DEFAULT_TRACKER = "ship";

/**
 * Native states we offer as datalist hints per tracker. These are the
 * canonical statuses each tracker ships with — operators can still
 * type a custom workflow state, but they don't have to guess names.
 *
 * Linear and Jira have a wider state vocabulary; we surface the most
 * common workflow groups. GitHub Issues only has open/closed.
 */
const TRACKER_NATIVE_HINTS: Record<string, string[]> = {
  linear: [
    "Backlog",
    "Todo",
    "In Progress",
    "In Review",
    "Done",
    "Cancelled",
    "Duplicate",
  ],
  jira: ["To Do", "In Progress", "In Review", "Blocked", "Done"],
  github_issues: ["open", "closed"],
  github: ["open", "closed"],
  notion: ["Not started", "In progress", "Done", "Archived"],
  ship: [
    "discovery",
    "specification",
    "planning",
    "implementation",
    "review",
    "merged",
    "blocked",
  ],
};

export function TrackerMappingPanel({
  workspaceId,
  process,
  repoId,
  config,
  trackerKind,
}: {
  workspaceId: string;
  process: ApiProcess;
  repoId?: string;
  config: ApiRepoConfig | null;
  /** Workspace's bound tracker (linear / jira / github / notion). When
   * omitted, falls back to the legacy "ship" sentinel. */
  trackerKind?: string;
}) {
  const canonicalStates = useMemo(() => collectCanonicalStates(process), [process]);
  const [tracker, setTracker] = useState(trackerKind?.trim() || DEFAULT_TRACKER);
  // Re-sync tracker pick when the workspace binding changes.
  useEffect(() => {
    if (trackerKind) setTracker(trackerKind);
  }, [trackerKind]);
  const datalistId = `tracker-states-${useId().replaceAll(":", "")}`;
  const nativeHints =
    TRACKER_NATIVE_HINTS[tracker.toLowerCase()] ?? TRACKER_NATIVE_HINTS.ship;
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

        <div className="flex flex-wrap items-end gap-3">
          <label className="min-w-0 flex-1">
            <span className="mb-1 block text-[10px] font-bold uppercase tracking-widest text-white/45">
              Tracker projection
              {trackerKind && (
                <span className="ml-2 font-normal normal-case tracking-normal text-white/35">
                  · workspace default
                </span>
              )}
            </span>
            <input
              value={tracker}
              onChange={(e) => {
                const next = e.target.value.trim() || DEFAULT_TRACKER;
                setTracker(next);
                setMapping((current) => ({
                  ...current,
                  [next]:
                    current[next] ??
                    Object.fromEntries(
                      canonicalStates.map((s) => [s, titleize(s)]),
                    ),
                }));
              }}
              className="w-full rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-sm text-white outline-none focus:border-aqua/40"
            />
          </label>
          <div
            className={[
              "shrink-0 rounded-full px-3 py-1.5 text-[11px] font-semibold",
              missing.length > 0
                ? "border border-coral/30 bg-coral/[0.07] text-coral"
                : "border border-emerald-400/30 bg-emerald-400/[0.07] text-emerald-300",
            ].join(" ")}
          >
            {missing.length > 0
              ? `${missing.length} unmapped`
              : "All states mapped"}
          </div>
        </div>

        <datalist id={datalistId}>
          {nativeHints.map((hint) => (
            <option key={hint} value={hint} />
          ))}
        </datalist>

        <TrackerMappingGraph
          process={process}
          mapping={activeMapping}
          datalistId={datalistId}
          onPatchCanonical={patchCanonical}
        />
      </div>
    </Card>
  );
}

function TrackerMappingGraph({
  process,
  mapping,
  datalistId,
  onPatchCanonical,
}: {
  process: ApiProcess;
  mapping: Record<string, string>;
  datalistId: string;
  onPatchCanonical: (canonical: string, native: string) => void;
}) {
  const exceptionStates = collectExceptionStates(process);
  return (
    <div className="overflow-hidden rounded-[1.5rem] border border-white/10 bg-[#050a15]">
      <div className="overflow-x-auto p-4">
        <div className="flex min-w-max items-stretch gap-3">
          {process.states.map((state, index) => (
            <div key={state.id} className="flex items-center gap-3">
              <MappingStateCard
                state={state}
                mapping={mapping}
                datalistId={datalistId}
                onPatchCanonical={onPatchCanonical}
              />
              {index < process.states.length - 1 ? (
                <div className="flex w-12 items-center">
                  <div className="h-px flex-1 bg-aqua/35" />
                  <div className="h-2 w-2 rotate-45 border-r border-t border-aqua/60" />
                </div>
              ) : null}
            </div>
          ))}
        </div>
      </div>

      {exceptionStates.length > 0 ? (
        <div className="border-t border-white/10 bg-black/20 p-4">
          <div className="mb-3 text-[10px] font-bold uppercase tracking-[0.22em] text-white/35">
            Shared exits
          </div>
          <div className="flex flex-wrap gap-2">
            {exceptionStates.map((canonical) => (
              <MappingChip
                key={canonical}
                canonical={canonical}
                mapping={mapping}
                datalistId={datalistId}
                tone={canonical.includes("blocked") ? "danger" : "warning"}
                onPatchCanonical={onPatchCanonical}
              />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function MappingStateCard({
  state,
  mapping,
  datalistId,
  onPatchCanonical,
}: {
  state: ApiProcess["states"][number];
  mapping: Record<string, string>;
  datalistId: string;
  onPatchCanonical: (canonical: string, native: string) => void;
}) {
  const ordered = orderedContractStates(state);
  return (
    <section className="w-[280px] rounded-2xl border border-white/10 bg-white/[0.03] p-3">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-white">{state.name}</div>
        <div className="mt-0.5 truncate text-[11px] text-white/40">
          {state.specialist_name}
        </div>
      </div>

      <div className="mt-3 space-y-2">
        {ordered.map((item, index) => (
          <div key={item.canonical} className="flex items-center gap-2">
            <MappingChip
              canonical={item.canonical}
              label={item.label}
              mapping={mapping}
              datalistId={datalistId}
              tone={item.tone}
              onPatchCanonical={onPatchCanonical}
            />
            {index < ordered.length - 1 ? (
              <div className="text-aqua/45">→</div>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}

function MappingChip({
  canonical,
  label,
  mapping,
  datalistId,
  tone = "normal",
  onPatchCanonical,
}: {
  canonical: string;
  label?: string;
  mapping: Record<string, string>;
  datalistId: string;
  tone?: "normal" | "success" | "warning" | "danger";
  onPatchCanonical: (canonical: string, native: string) => void;
}) {
  const toneClass =
    tone === "success"
      ? "border-aqua/25 bg-aqua/[0.08]"
      : tone === "warning"
        ? "border-amber-300/25 bg-amber-300/[0.07]"
        : tone === "danger"
          ? "border-coral/25 bg-coral/[0.07]"
          : "border-white/10 bg-black/25";
  return (
    <label className={`min-w-0 flex-1 rounded-xl border p-2 ${toneClass}`}>
      <span className="block text-[9px] font-bold uppercase tracking-[0.18em] text-white/35">
        {label ?? "Ship state"}
      </span>
      <span className="mt-1 block truncate font-mono text-[11px] text-white/70">
        {canonical}
      </span>
      <input
        value={mapping[canonical] ?? ""}
        onChange={(event) => onPatchCanonical(canonical, event.target.value)}
        placeholder={titleize(canonical)}
        list={datalistId}
        className="mt-2 w-full rounded-lg border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white outline-none transition placeholder:text-white/25 focus:border-aqua/40 focus:bg-aqua/[0.04]"
      />
    </label>
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

function orderedContractStates(state: ApiProcess["states"][number]) {
  const contract = state.ticket_contract;
  if (!contract) return [];
  return [
    contract.input_state
      ? {
          canonical: contract.input_state,
          label: "Input",
          tone: "normal" as const,
        }
      : null,
    contract.claim_state
      ? {
          canonical: contract.claim_state,
          label: "Claim",
          tone: "warning" as const,
        }
      : null,
    contract.success_state
      ? {
          canonical: contract.success_state,
          label: "Success",
          tone: "success" as const,
        }
      : null,
  ].filter(
    (item): item is { canonical: string; label: string; tone: "normal" | "success" | "warning" } =>
      item != null,
  );
}

function collectExceptionStates(process: ApiProcess) {
  const out = new Set<string>();
  for (const state of process.states) {
    const contract = state.ticket_contract;
    if (!contract) continue;
    if (contract.blocked_state) out.add(contract.blocked_state);
    if (contract.needs_info_state) out.add(contract.needs_info_state);
    if (contract.approval_state) out.add(contract.approval_state);
  }
  return Array.from(out).sort();
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
