/**
 * URL helpers for the outcome-first ``/runs`` list (RFC-0010 / Wave 6
 * Phase 3 ticket P3-06).
 *
 * The list page is URL-driven so admins can deep-link / share /
 * refresh without losing context. Both the server component
 * (``/app/runs/page.tsx``) and the client filter wrapper
 * (``./runs-filters-controlled.tsx``) need the same encoding so we
 * keep it in one pure helper module — no React, no ``'use client'``.
 *
 * Mirrors the conventions established by ``inbox-url.ts``:
 *   - Default values are omitted (so ``/runs`` == "everything").
 *   - Single-select dimensions (``play``, ``repo``, ``has_escalations``)
 *     emit at most one query param.
 *   - Multi-select dimensions (``status``, ``trigger``) emit a single
 *     comma-joined param value to keep the URL compact and to match
 *     the API contract once sibling-A wires server-side filtering.
 *   - Unknown / empty filter sets stay out of the URL entirely so the
 *     "clear filters" link resolves to the bare ``/runs`` path.
 */

export const RUN_STATUSES = [
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;
export type RunStatus = (typeof RUN_STATUSES)[number];

export const RUN_TRIGGERS = [
  "manual",
  "webhook",
  "cron",
  "onboarding",
] as const;
export type RunTrigger = (typeof RUN_TRIGGERS)[number];

export type RunsFilterState = {
  /** Single-select pipeline / play key (currently the pipeline id). */
  play: string | null;
  /** Single-select activated-repo id (UUID, not the slug). */
  repo: string | null;
  /** Multi-select run-status enum. */
  statuses: RunStatus[];
  /** Multi-select trigger enum. */
  triggers: RunTrigger[];
  /** Boolean toggle — when true the row must have at least one escalation. */
  hasEscalations: boolean;
};

export const DEFAULT_RUNS_FILTERS: RunsFilterState = {
  play: null,
  repo: null,
  statuses: [],
  triggers: [],
  hasEscalations: false,
};

export function isRunStatus(value: string): value is RunStatus {
  return (RUN_STATUSES as readonly string[]).includes(value);
}

export function isRunTrigger(value: string): value is RunTrigger {
  return (RUN_TRIGGERS as readonly string[]).includes(value);
}

function dedupe<T>(arr: T[]): T[] {
  return Array.from(new Set(arr));
}

function csvParam<T extends string>(
  raw: string | string[] | undefined,
  guard: (v: string) => v is T,
): T[] {
  if (raw === undefined) return [];
  const parts = Array.isArray(raw) ? raw : raw.split(",");
  return dedupe(
    parts
      .flatMap((p) => p.split(","))
      .map((p) => p.trim())
      .filter((p) => p.length > 0)
      .filter(guard),
  );
}

/**
 * Parse the (defensively-typed) Next.js ``searchParams`` bag into a
 * normalised ``RunsFilterState``. Anything we don't recognise is
 * dropped silently — a hostile URL can't smuggle bad enum values into
 * the FE filter pipeline (or, when sibling-A wires it, into the API
 * query string).
 */
export function parseRunsSearchParams(
  raw: Record<string, string | string[] | undefined>,
): RunsFilterState {
  const playRaw = typeof raw.play === "string" ? raw.play : null;
  const repoRaw = typeof raw.repo === "string" ? raw.repo : null;
  const statuses = csvParam(raw.status, isRunStatus);
  const triggers = csvParam(raw.trigger, isRunTrigger);
  const hasEsc =
    typeof raw.has_escalations === "string" &&
    raw.has_escalations.toLowerCase() === "true";
  return {
    play: playRaw && playRaw.length > 0 ? playRaw : null,
    repo: repoRaw && repoRaw.length > 0 ? repoRaw : null,
    statuses,
    triggers,
    hasEscalations: hasEsc,
  };
}

export function buildRunsUrl(filters: RunsFilterState): string {
  const params = new URLSearchParams();
  if (filters.play) params.set("play", filters.play);
  if (filters.repo) params.set("repo", filters.repo);
  if (filters.statuses.length > 0)
    params.set("status", filters.statuses.join(","));
  if (filters.triggers.length > 0)
    params.set("trigger", filters.triggers.join(","));
  if (filters.hasEscalations) params.set("has_escalations", "true");
  const qs = params.toString();
  return qs ? `/runs?${qs}` : "/runs";
}

/**
 * Count of non-default filter axes — drives the "clear filters" link
 * visibility and the empty-state copy. Each axis (play, repo, status,
 * trigger, has_escalations) contributes at most 1.
 */
export function countActiveRunsFilters(filters: RunsFilterState): number {
  let n = 0;
  if (filters.play) n += 1;
  if (filters.repo) n += 1;
  if (filters.statuses.length > 0) n += 1;
  if (filters.triggers.length > 0) n += 1;
  if (filters.hasEscalations) n += 1;
  return n;
}
