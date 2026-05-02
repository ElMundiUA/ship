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

export function validateProcess({
  stages,
  transitions,
}: {
  stages: ApiProcessState[];
  transitions: ApiProcessTransition[];
}): ValidationResult {
  const errors: ValidationItem[] = [];
  const warnings: ValidationItem[] = [];
  const stageById = new Map(stages.map((s) => [s.id, s] as const));

  // ── Stage-level checks ──────────────────────────────────────────────
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
        message: `${count} stages share id "${id}" — every stage needs a unique id so transitions resolve.`,
        anchor: { kind: "stage", id },
      });
    }
  }

  // ── Reachability + termination ─────────────────────────────────────
  const incoming = new Map<string, ApiProcessTransition[]>();
  const outgoing = new Map<string, ApiProcessTransition[]>();
  for (const t of transitions) {
    if (!stageById.has(t.from_state_id)) {
      errors.push({
        kind: "error",
        code: "transition.dangling_from",
        message: `Transition ${t.id} starts at non-existent stage "${t.from_state_id}".`,
        anchor: { kind: "transition", id: t.id },
      });
      continue;
    }
    if (!stageById.has(t.to_state_id)) {
      errors.push({
        kind: "error",
        code: "transition.dangling_to",
        message: `Transition ${t.id} points to non-existent stage "${t.to_state_id}".`,
        anchor: { kind: "transition", id: t.id },
      });
      continue;
    }
    outgoing.set(
      t.from_state_id,
      [...(outgoing.get(t.from_state_id) ?? []), t],
    );
    incoming.set(
      t.to_state_id,
      [...(incoming.get(t.to_state_id) ?? []), t],
    );
  }

  for (const stage of stages) {
    const inc = incoming.get(stage.id) ?? [];
    const out = outgoing.get(stage.id) ?? [];
    // Backlog stages are entry points — they can have zero incoming.
    if (stage.state !== "backlog" && inc.length === 0) {
      errors.push({
        kind: "error",
        code: "stage.no_incoming",
        message: `"${stage.name}" has no incoming transition — work can never reach it.`,
        anchor: { kind: "stage", id: stage.id },
      });
    }
    // Closed stages are terminal — they can have zero outgoing.
    if (stage.state !== "closed" && out.length === 0) {
      errors.push({
        kind: "error",
        code: "stage.no_outgoing",
        message: `"${stage.name}" has no outgoing transition — work will get stuck here.`,
        anchor: { kind: "stage", id: stage.id },
      });
    }
  }

  // ── Process-level shape ────────────────────────────────────────────
  const hasBacklog = stages.some((s) => s.state === "backlog");
  const hasClosed = stages.some((s) => s.state === "closed");
  if (!hasBacklog) {
    warnings.push({
      kind: "warning",
      code: "process.no_backlog",
      message:
        "No stage is in the Backlog lane — operators won't have a clean entry point for new tickets.",
    });
  }
  if (!hasClosed) {
    errors.push({
      kind: "error",
      code: "process.no_closed",
      message:
        "No stage is in the Closed lane — work can never finish. Add a terminal stage.",
    });
  }

  // ── Actor / lane consistency warnings ──────────────────────────────
  for (const t of transitions) {
    const from = stageById.get(t.from_state_id);
    const to = stageById.get(t.to_state_id);
    if (!from || !to) continue;
    // Backlog → planning is human-only by convention.
    if (
      from.state === "backlog" &&
      to.state === "planning" &&
      t.trigger_actor !== "user"
    ) {
      warnings.push({
        kind: "warning",
        code: "transition.backlog_should_be_user",
        message: `"${from.name}" → "${to.name}" leaves the backlog — usually only the operator triggers this. Set actor = user?`,
        anchor: { kind: "transition", id: t.id },
      });
    }
    // Reviewing → closed is human-only by convention.
    if (
      from.state === "reviewing" &&
      to.state === "closed" &&
      t.trigger_actor !== "user"
    ) {
      warnings.push({
        kind: "warning",
        code: "transition.close_should_be_user",
        message: `"${from.name}" → "${to.name}" closes the ticket — usually only a human approves. Set actor = user?`,
        anchor: { kind: "transition", id: t.id },
      });
    }
  }

  return { errors, warnings };
}

function isCanonicalState(value: unknown): value is CanonicalState {
  return typeof value === "string" && (CANONICAL_STATES as readonly string[]).includes(value);
}
