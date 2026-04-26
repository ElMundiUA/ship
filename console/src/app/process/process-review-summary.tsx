import type { ApiProcess } from "@/lib/api/client";

export function ProcessReviewSummary({
  initial,
  draft,
  changedAreas,
}: {
  initial: ApiProcess;
  draft: ApiProcess;
  changedAreas: string[];
}) {
  const changes = summarizeProcessChanges(initial, draft, changedAreas);
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.025] p-3">
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        Review before publishing
      </div>
      {changes.length === 0 ? (
        <p className="mt-2 text-xs text-white/45">
          No process changes yet. Edits will be summarized here before publishing.
        </p>
      ) : (
        <ul className="mt-2 space-y-1 text-xs text-white/55">
          {changes.map((change) => (
            <li key={change}>- {change}</li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-[11px] text-white/35">
        Publishing opens a reviewable process change for this repo; technical config
        details stay in the PR.
      </p>
    </div>
  );
}

export function processChangeSummary(
  initial: ApiProcess,
  draft: ApiProcess,
  changedAreas: string[],
) {
  const changes = summarizeProcessChanges(initial, draft, changedAreas);
  return changes.length ? changes.join("; ") : "Publish process changes";
}

function summarizeProcessChanges(
  initial: ApiProcess,
  draft: ApiProcess,
  changedAreas: string[],
) {
  const changes = new Set<string>();
  for (const area of changedAreas) changes.add(area);
  if (initial.name !== draft.name || initial.primary !== draft.primary) {
    changes.add("Process settings changed");
  }
  if (initial.states.length !== draft.states.length) {
    changes.add("Flow steps changed");
  } else {
    const before = JSON.stringify(initial.states);
    const after = JSON.stringify(draft.states);
    if (before !== after) changes.add("Flow step details changed");
  }
  if (JSON.stringify(initial.transitions) !== JSON.stringify(draft.transitions)) {
    changes.add("Flow handoffs changed");
  }
  if (JSON.stringify(initial.schedule ?? null) !== JSON.stringify(draft.schedule ?? null)) {
    changes.add("Flow schedule slots changed");
  }
  if (JSON.stringify(initial.routines) !== JSON.stringify(draft.routines)) {
    changes.add("Standalone routines changed");
  }
  if (
    JSON.stringify(initial.tracker_mapping ?? null) !==
    JSON.stringify(draft.tracker_mapping ?? null)
  ) {
    changes.add("Tracker state mapping changed");
  }
  return Array.from(changes);
}
