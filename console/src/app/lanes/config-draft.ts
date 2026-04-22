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
 * custom fields (``pattern``, ``idempotency_key``, ``once``, etc.)
 * round-trip verbatim — the UI only ever edits ``enabled`` and
 * ``schedule``.
 */

import type {
  ApiLane,
  ApiLaneCatalogEntry,
  ApiRepoConfig,
} from "@/lib/api/client";

export type LaneDraft = {
  enabled: boolean;
  schedule: string | null;
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
    baseline[entry.kind] = {
      enabled: inConfig,
      schedule: entry.schedule
        ? scheduleFromConfig(parsed) ?? entry.schedule
        : null,
      origin: "recipe",
      rawConfig: isPlainObject(parsed) ? parsed : null,
    };
  }

  for (const laneId of Object.keys(parsedLanes)) {
    if (baseline[laneId]) continue;
    const parsed = parsedLanes[laneId];
    baseline[laneId] = {
      enabled: true,
      schedule: scheduleFromConfig(parsed),
      origin: "config-only",
      rawConfig: isPlainObject(parsed) ? parsed : null,
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
}): Record<string, Record<string, string | null>> {
  const out: Record<string, Record<string, string | null>> = {};
  const catalogIndex = new Map(catalog.map((e) => [e.kind, e]));

  for (const [laneId, d] of Object.entries(draft)) {
    if (!d.enabled) continue;
    const entry = catalogIndex.get(laneId);
    if (entry) {
      out[laneId] = laneTriggerFromRecipe(entry, d);
      continue;
    }
    const preserved = preserveRawTrigger(d.rawConfig);
    if (preserved) out[laneId] = preserved;
  }
  return out;
}

function laneTriggerFromRecipe(
  entry: ApiLaneCatalogEntry,
  draft: LaneDraft,
): Record<string, string | null> {
  const trigger: Record<string, string | null> = {};
  if (entry.schedule) {
    trigger.schedule = draft.schedule ?? entry.schedule;
  } else if (entry.event) {
    trigger.event = entry.event;
  }
  const raw = draft.rawConfig;
  const rawPattern =
    raw && typeof raw.pattern === "string" ? raw.pattern : null;
  const rawIdem =
    raw && typeof raw.idempotency_key === "string" ? raw.idempotency_key : null;
  if (rawPattern ?? entry.pattern) {
    trigger.pattern = rawPattern ?? entry.pattern;
  }
  if (rawIdem ?? entry.idempotency_key) {
    trigger.idempotency_key = rawIdem ?? entry.idempotency_key;
  }
  return trigger;
}

function preserveRawTrigger(
  raw: Record<string, unknown> | null,
): Record<string, string | null> | null {
  if (!raw) return null;
  const out: Record<string, string | null> = {};
  const keys = [
    "once",
    "event",
    "schedule",
    "pattern",
    "idempotency_key",
  ] as const;
  for (const k of keys) {
    const v = raw[k];
    if (typeof v === "string") out[k] = v;
  }
  const triggerKinds = ["once", "event", "schedule"].filter((k) => out[k]);
  if (triggerKinds.length !== 1) return null;
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
