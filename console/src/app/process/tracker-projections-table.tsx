"use client";

import { useState } from "react";

import { CANONICAL_STATES, TRACKER_OVERLAY, type CanonicalState } from "@/lib/api/types";

const TRACKER_KINDS = ["linear", "jira", "github", "notion"] as const;
type TrackerKind = (typeof TRACKER_KINDS)[number];

const TRACKER_LABEL: Record<TrackerKind, string> = {
  linear: "Linear",
  jira: "Jira",
  github: "GitHub Issues",
  notion: "Notion",
};

const STATE_LABEL: Record<CanonicalState, string> = {
  backlog: "Backlog",
  planning: "Planning",
  executing: "Executing",
  reviewing: "Reviewing",
  awaiting_input: "Awaiting input",
  blocked: "Blocked",
  closed: "Closed",
};

const STATE_HINT: Record<CanonicalState, string> = {
  backlog: "Untouched, agents ignore",
  planning: "Intake / BA / architects scope",
  executing: "Dev / QA cycles",
  reviewing: "Awaiting human approval",
  awaiting_input: "Frozen on clarification",
  blocked: "Frozen on external blocker",
  closed: "Terminal",
};

/**
 * Tracker projection table — 7 canonical states × 4 tracker kinds.
 *
 * Click on a chip to edit the native column / status name inline. Save
 * fires on blur or Enter; Esc cancels. Empty value clears the override
 * and reverts to "unmapped" (which the operator should rarely want;
 * the defaults from the backend adapter cover stock workflows).
 *
 * Overlay states (awaiting_input, blocked) project to the magic
 * ``__overlay__`` value the FE renders as "stays in current column +
 * label overlay" — these can be edited too if a team wants a
 * different label namespace.
 */
export function TrackerProjectionsTable({
  trackerMapping,
  defaultTracker,
  onChange,
}: {
  /** Backend's tracker_mapping field on ApiProcess. */
  trackerMapping: Record<string, Record<string, string>>;
  /** Which tracker tab opens by default — usually the workspace's bound one. */
  defaultTracker?: TrackerKind;
  /** Fired when the operator edits a cell. Empty value = clear override. */
  onChange?: (
    tracker: string,
    canonicalState: string,
    nextValue: string,
  ) => void;
}) {
  const [tracker, setTracker] = useState<TrackerKind>(defaultTracker ?? "linear");
  const [editing, setEditing] = useState<string | null>(null);
  const projection = trackerMapping[tracker] ?? {};

  function commit(state: CanonicalState, value: string) {
    if (onChange) onChange(tracker, state, value);
    setEditing(null);
  }

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/55">
            Tracker projections
          </div>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-white/45">
            Click a value to edit. Defaults work for stock workflows;
            override only when your team renamed a workflow status.
            ``stays + label`` means the ticket doesn&apos;t move between
            columns — the adapter just flips a label.
          </p>
        </div>
        <div className="flex shrink-0 gap-1 rounded-full border border-white/10 bg-white/[0.04] p-1">
          {TRACKER_KINDS.map((kind) => (
            <button
              key={kind}
              type="button"
              onClick={() => {
                setTracker(kind);
                setEditing(null);
              }}
              className={[
                "rounded-full px-3 py-1 text-[11px] font-semibold transition",
                tracker === kind
                  ? "bg-aqua/20 text-aqua"
                  : "text-white/55 hover:bg-white/[0.05] hover:text-white",
              ].join(" ")}
            >
              {TRACKER_LABEL[kind]}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 overflow-hidden rounded-xl border border-white/10">
        <table className="min-w-full text-xs">
          <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
            <tr>
              <th className="w-1/3 px-3 py-2 text-left font-semibold">
                Canonical state
              </th>
              <th className="px-3 py-2 text-left font-semibold">
                {TRACKER_LABEL[tracker]} projection
              </th>
            </tr>
          </thead>
          <tbody>
            {CANONICAL_STATES.map((state) => {
              const value = projection[state];
              const isOverlay = value === TRACKER_OVERLAY;
              const isEditing = editing === state;
              return (
                <tr
                  key={state}
                  className="border-t border-white/[0.06] hover:bg-white/[0.02]"
                >
                  <td className="px-3 py-2.5 align-top">
                    <div className="flex items-center gap-2">
                      <span
                        className="h-1.5 w-1.5 shrink-0 rounded-full"
                        style={{ background: STATE_DOT[state] }}
                        aria-hidden
                      />
                      <span className="text-sm font-semibold text-white">
                        {STATE_LABEL[state]}
                      </span>
                    </div>
                    <div className="mt-0.5 pl-3.5 text-[11px] text-white/40">
                      {STATE_HINT[state]}
                    </div>
                  </td>
                  <td className="px-3 py-2.5 align-top">
                    {isEditing ? (
                      <ProjectionInput
                        initialValue={isOverlay ? "" : value ?? ""}
                        onCommit={(next) => commit(state, next)}
                        onCancel={() => setEditing(null)}
                      />
                    ) : isOverlay ? (
                      <button
                        type="button"
                        onClick={() => setEditing(state)}
                        className="inline-flex items-center gap-1.5 rounded-full border border-amber-300/30 bg-amber-300/[0.08] px-2.5 py-1 text-[11px] font-semibold text-amber-200 transition hover:border-amber-300/50 hover:bg-amber-300/[0.12]"
                        title="Click to set a native column instead of the overlay default"
                      >
                        stays in current column
                        <span className="font-normal text-amber-200/60">
                          + label overlay
                        </span>
                      </button>
                    ) : value ? (
                      <button
                        type="button"
                        onClick={() => setEditing(state)}
                        className="inline-flex items-center gap-1 rounded-md border border-aqua/25 bg-aqua/[0.08] px-2 py-0.5 font-mono text-[11px] font-semibold text-aqua transition hover:border-aqua/45 hover:bg-aqua/[0.14]"
                        title="Click to edit"
                      >
                        {value}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setEditing(state)}
                        className="text-[11px] italic text-white/35 hover:text-white/65"
                        title="Click to set a native column"
                      >
                        unmapped — click to set
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ProjectionInput({
  initialValue,
  onCommit,
  onCancel,
}: {
  initialValue: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  return (
    <input
      autoFocus
      type="text"
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => onCommit(value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          onCommit(value);
        } else if (event.key === "Escape") {
          event.preventDefault();
          onCancel();
        }
      }}
      placeholder="e.g. In Progress"
      className="w-48 rounded-md border border-aqua/40 bg-aqua/[0.04] px-2 py-1 font-mono text-[11px] font-semibold text-white outline-none focus:border-aqua/70"
    />
  );
}

const STATE_DOT: Record<CanonicalState, string> = {
  backlog: "rgba(255,255,255,0.45)",
  planning: "rgba(99, 200, 255, 0.85)",
  executing: "rgba(207, 169, 107, 0.95)",
  reviewing: "rgba(168, 85, 247, 0.85)",
  awaiting_input: "rgba(255, 196, 87, 0.85)",
  blocked: "rgba(244, 114, 114, 0.85)",
  closed: "rgba(120, 200, 140, 0.85)",
};
