/**
 * Shared helpers for composing ``.ship/config.yml`` proposal bodies.
 *
 * Both the Active tab's "Edit schedule" drawer and the Library tab's
 * "Add/Edit/Remove" card flow use these to:
 *
 * 1. Read the current ``lanes:`` mapping into an in-memory baseline.
 * 2. Apply a single change on top of that baseline.
 * 3. Emit the full ``lanes`` payload the backend expects from
 *    ``POST /v1/.../repos/{id}/config/propose`` (it *replaces* the
 *    entire mapping, so we must round-trip every existing entry).
 *
 * The baseline preserves the raw YAML fragment for each lane so
 * custom fields (``pattern``, ``patterns``, ``fanout``,
 * ``idempotency_key``, ``once``, etc.) round-trip verbatim — the UI
 * edits ``enabled``, ``schedule``, and (for ≥2-pattern lanes)
 * ``fanout``. Single-pattern lanes never emit a ``fanout`` key so we
 * don't add noise to diffs.
 */

import type {
  ApiLane,
  ApiLaneCatalogEntry,
  ApiLaneTriggerIn,
  ApiRepoConfig,
} from "@/lib/api/client";

export type FanoutMode = "matrix" | "sequential" | "concurrent";

export const FANOUT_MODES: FanoutMode[] = [
  "matrix",
  "sequential",
  "concurrent",
];

export const DEFAULT_FANOUT: FanoutMode = "matrix";

export type LaneDraft = {
  enabled: boolean;
  schedule: string | null;
  /** Patterns in order; single entry for single-pattern lanes. */
  patterns: string[];
  /** Effective fan-out mode — always resolved, never null. */
  fanout: FanoutMode;
  origin: "recipe" | "config-only";
  rawConfig: Record<string, unknown> | null;
};

// ----------------------------------------------------------------------------
// baseline
// ----------------------------------------------------------------------------

export function buildBaseline(
  catalog: ApiLaneCatalogEntry[],
  config: ApiRepoConfig | null,
  lanes: ApiLane[],
): Record<string, LaneDraft> {
  const baseline: Record<string, LaneDraft> = {};
  const parsedLanes = config?.parsed?.lanes ?? {};

  for (const entry of catalog) {
    const parsed = parsedLanes[entry.kind];
    const inConfig = parsed !== undefined;
    const raw = isPlainObject(parsed) ? parsed : null;
    baseline[entry.kind] = {
      enabled: inConfig,
      schedule: entry.schedule
        ? scheduleFromConfig(parsed) ?? entry.schedule
        : null,
      patterns: patternsFromRaw(raw, entry.pattern),
      fanout: fanoutFromRaw(raw),
      origin: "recipe",
      rawConfig: raw,
    };
  }

  for (const laneId of Object.keys(parsedLanes)) {
    if (baseline[laneId]) continue;
    const parsed = parsedLanes[laneId];
    const raw = isPlainObject(parsed) ? parsed : null;
    baseline[laneId] = {
      enabled: true,
      schedule: scheduleFromConfig(parsed),
      patterns: patternsFromRaw(raw, null),
      fanout: fanoutFromRaw(raw),
      origin: "config-only",
      rawConfig: raw,
    };
  }

  void lanes;
  return baseline;
}

function scheduleFromConfig(raw: unknown): string | null {
  if (!raw || typeof raw !== "object") return null;
  const sched = (raw as Record<string, unknown>).schedule;
  return typeof sched === "string" ? sched : null;
}

function patternsFromRaw(
  raw: Record<string, unknown> | null,
  recipeDefault: string | null,
): string[] {
  if (raw) {
    const list = raw.patterns;
    if (Array.isArray(list)) {
      const out = list.filter(
        (x): x is string => typeof x === "string" && x.trim().length > 0,
      );
      if (out.length) return out;
    }
    const single = raw.pattern;
    if (typeof single === "string" && single.trim()) return [single];
  }
  return recipeDefault ? [recipeDefault] : [];
}

function fanoutFromRaw(raw: Record<string, unknown> | null): FanoutMode {
  if (!raw) return DEFAULT_FANOUT;
  const v = raw.fanout;
  if (typeof v === "string" && (FANOUT_MODES as string[]).includes(v)) {
    return v as FanoutMode;
  }
  return DEFAULT_FANOUT;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

// ----------------------------------------------------------------------------
// proposal body
// ----------------------------------------------------------------------------

export function buildProposalBody({
  catalog,
  draft,
}: {
  catalog: ApiLaneCatalogEntry[];
  draft: Record<string, LaneDraft>;
}): Record<string, ApiLaneTriggerIn> {
  const out: Record<string, ApiLaneTriggerIn> = {};
  const catalogIndex = new Map(catalog.map((e) => [e.kind, e]));

  for (const [laneId, d] of Object.entries(draft)) {
    if (!d.enabled) continue;
    const entry = catalogIndex.get(laneId);
    if (entry) {
      out[laneId] = laneTriggerFromRecipe(entry, d);
      continue;
    }
    const preserved = preserveRawTrigger(d);
    if (preserved) out[laneId] = preserved;
  }
  return out;
}

function laneTriggerFromRecipe(
  entry: ApiLaneCatalogEntry,
  draft: LaneDraft,
): ApiLaneTriggerIn {
  const trigger: ApiLaneTriggerIn = {};
  if (entry.schedule) {
    trigger.schedule = draft.schedule ?? entry.schedule;
  } else if (entry.event) {
    trigger.event = entry.event;
  }
  // Pattern resolution order:
  //   1. draft.patterns (the authoritative list the UI maintains)
  //   2. recipe default
  // We always send ``patterns`` as a list when it has ≥2 entries and
  // fall back to scalar ``pattern`` for the single-pattern case so
  // the YAML the backend emits stays minimal/stable.
  const patterns = draft.patterns.length
    ? draft.patterns
    : entry.pattern
      ? [entry.pattern]
      : [];
  if (patterns.length >= 2) trigger.patterns = patterns;
  else if (patterns.length === 1) trigger.pattern = patterns[0];

  const raw = draft.rawConfig;
  const rawIdem =
    raw && typeof raw.idempotency_key === "string" ? raw.idempotency_key : null;
  if (rawIdem ?? entry.idempotency_key) {
    trigger.idempotency_key = rawIdem ?? entry.idempotency_key;
  }
  // RFC-0008 C3.2 — fanout only meaningful for multi-pattern lanes,
  // and the backend omits it when it equals the default.
  if (patterns.length >= 2 && draft.fanout !== DEFAULT_FANOUT) {
    trigger.fanout = draft.fanout;
  }
  return trigger;
}

function preserveRawTrigger(draft: LaneDraft): ApiLaneTriggerIn | null {
  const raw = draft.rawConfig;
  if (!raw) return null;
  const out: ApiLaneTriggerIn = {};
  // Trigger discriminator (exactly one of once/event/schedule must be
  // present — we dropped a lane whose raw YAML fails this invariant
  // rather than silently materialise a broken entry).
  for (const k of ["once", "event", "schedule"] as const) {
    const v = raw[k];
    if (typeof v === "string") out[k] = v;
  }
  const triggerKinds = (["once", "event", "schedule"] as const).filter(
    (k) => out[k],
  );
  if (triggerKinds.length !== 1) return null;

  if (typeof raw.idempotency_key === "string") {
    out.idempotency_key = raw.idempotency_key;
  }
  // Emit the UI-tracked pattern list (which already reflects any edit
  // the user made) instead of the raw one. Same scalar-vs-list rule
  // the recipe path uses, so diffs stay minimal.
  if (draft.patterns.length >= 2) out.patterns = draft.patterns;
  else if (draft.patterns.length === 1) out.pattern = draft.patterns[0];

  if (draft.patterns.length >= 2 && draft.fanout !== DEFAULT_FANOUT) {
    out.fanout = draft.fanout;
  }
  return out;
}

// ----------------------------------------------------------------------------
// propose API helper (single-card flow)
// ----------------------------------------------------------------------------

export type ProposeResult =
  | { ok: true; pr_url: string; pr_number: number }
  | { ok: false; code?: string; error: string };

/**
 * Submit a single-change proposal to ``/api/lanes/propose``.
 *
 * The caller already composed the target ``draft`` (baseline +
 * modification) — we just serialise, POST, and normalise the
 * response so the UI has a consistent shape to render banners from.
 */
export async function submitProposal({
  workspaceId,
  repoId,
  baseSha,
  catalog,
  draft,
  changeSummary,
}: {
  workspaceId: string;
  repoId: string;
  baseSha: string | null;
  catalog: ApiLaneCatalogEntry[];
  draft: Record<string, LaneDraft>;
  changeSummary?: string;
}): Promise<ProposeResult> {
  const lanesBody = buildProposalBody({ catalog, draft });
  const response = await fetch("/api/lanes/propose", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      workspaceId,
      repoId,
      base_sha: baseSha,
      lanes: lanesBody,
      change_summary: changeSummary?.trim() || undefined,
    }),
  });
  const body = (await response.json().catch(() => null)) as
    | {
        pr_url?: string;
        pr_number?: number;
        error?: string;
        code?: string;
      }
    | null;
  if (!response.ok || !body?.pr_url || typeof body.pr_number !== "number") {
    return {
      ok: false,
      code: body?.code,
      error: body?.error ?? `Couldn't open the PR (HTTP ${response.status}).`,
    };
  }
  return { ok: true, pr_url: body.pr_url, pr_number: body.pr_number };
}
