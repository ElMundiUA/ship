// Process FSM validation. Pure functions — no React, no I/O — so they
// can be unit-tested in isolation and re-run on any draft state. The
// editor renders the result through ProcessValidationPanel and gates
// the Publish button on errors.length === 0.

import type { ApiProcessState, ApiProcessTransition } from "@/lib/api/client";
import { CANONICAL_STATES, type CanonicalState } from "@/lib/api/types";

export type ValidationKind = "error" | "warning";

export type ValidationItem = {
  kind: ValidationKind;
  /** Stable code for telemetry / e2e selectors. */
  code: string;
  /** Operator-facing text — short imperative. */
  message: string;
  /** Optional stage / transition this complaint anchors to, so the
   *  panel can offer "Jump to Intake" links. */
  anchor?: { kind: "stage" | "transition"; id: string };
};

export type ValidationResult = {
  errors: ValidationItem[];
  warnings: ValidationItem[];
};

// Lanes that can host a terminal stage. Reviewing counts because in
// practice "human approves the review and the ticket is done" is a
// perfectly valid terminal — Linear flips the column to Done from the
// projection layer, no Ship stage needed in Closed.
const TERMINAL_LANES: ReadonlySet<CanonicalState> = new Set([
  "reviewing",
  "closed",
]);

export function validateProcess({
  stages,
  transitions: _transitions,
}: {
  stages: ApiProcessState[];
  transitions: ApiProcessTransition[];
}): ValidationResult {
  const errors: ValidationItem[] = [];
  const warnings: ValidationItem[] = [];

  // ── Stage-level checks ──────────────────────────────────────────────
  // Validity of the canonical state field; uniqueness of stage ids.
  // These are the only structural checks that survive the move to
  // implicit transitions — the old "Intake has no incoming" / "Final
  // Review has no outgoing" complaints don't apply when the chain is
  // derived from column order rather than an explicit transitions
  // array.
  const idCounts = new Map<string, number>();
  for (const stage of stages) {
    idCounts.set(stage.id, (idCounts.get(stage.id) ?? 0) + 1);
    if (!isCanonicalState(stage.state)) {
      errors.push({
        kind: "error",
        code: "stage.invalid_state",
        message: `Stage "${stage.name}" has an unknown lifecycle state (${String(stage.state)}). Drag it into one of the seven lanes.`,
        anchor: { kind: "stage", id: stage.id },
      });
    }
  }
  for (const [id, count] of idCounts) {
    if (count > 1) {
      errors.push({
        kind: "error",
        code: "stage.duplicate_id",
        message: `${count} stages share id "${id}" — every stage needs a unique id.`,
        anchor: { kind: "stage", id },
      });
    }
  }

  // ── Process-level shape (warnings only) ────────────────────────────
  // Empty processes can't run; surface as an error.
  if (stages.length === 0) {
    errors.push({
      kind: "error",
      code: "process.no_stages",
      message:
        "Process has no stages. Add at least one — drop a stage in any lane to start.",
    });
    return { errors, warnings };
  }

  // No terminal lane = work can't finish through Ship's projection.
  // It's a strong signal but not a blocker — some workspaces use the
  // tracker's native "Done" without a corresponding Ship stage.
  const hasTerminal = stages.some((s) => TERMINAL_LANES.has(s.state));
  if (!hasTerminal) {
    warnings.push({
      kind: "warning",
      code: "process.no_terminal",
      message:
        "No stage is in Reviewing or Closed — tickets won't have a Ship-side terminal. Tracker projection will still close them, but Ship runs won't see a finished state.",
    });
  }

  // No backlog stage = no operator-side "incoming inbox". Optional
  // warning — for processes that pull from the tracker directly, this
  // is actually fine.
  const hasBacklog = stages.some((s) => s.state === "backlog");
  if (!hasBacklog) {
    warnings.push({
      kind: "warning",
      code: "process.no_backlog",
      message:
        "No stage is in the Backlog lane — operators rely on the tracker's own backlog instead of a Ship-managed entry point.",
    });
  }

  return { errors, warnings };
}

function isCanonicalState(value: unknown): value is CanonicalState {
  return typeof value === "string" && (CANONICAL_STATES as readonly string[]).includes(value);
}
