/**
 * Canonical routine catalog — six routines, mirrored against backend
 * ``DEFAULT_SEED_LANES`` in ``backend/app/services/lane_recipes.py``.
 *
 * IDs are short verbs that match the backend seed exactly. They are
 * the runtime identifiers ``shipctl`` reads out of ``.ship/config.yml``,
 * so any change here MUST be paired with the same rename in the
 * backend seed AND a config-rewrite for any already-seeded repo.
 */
export const BUILTIN_ROUTINE_CATALOG: {
  id: string;
  name: string;
  description: string;
  /** Default agent prompt (stored as `prompt` in config). */
  prompt: string;
  defaultCron: string;
}[] = [
  {
    id: "daily",
    name: "Daily",
    description: "Morning digest of in-flight work, blockers, and risks.",
    prompt: "Summarize in-flight work, blockers, and risks for the team.",
    defaultCron: "0 9 * * *",
  },
  {
    id: "retro",
    name: "Retro",
    description: "End-of-day retro: what went well, what to improve, next actions.",
    prompt: "Run a short retro: what went well, what to improve, next actions.",
    defaultCron: "0 18 * * *",
  },
  {
    id: "healthcheck",
    name: "Healthcheck",
    description: "Reconcile CI, workflows, and guardrails after failed runs.",
    prompt:
      "Reconcile CI, workflows, and guardrails; open minimal fixes for broken gates.",
    defaultCron: "0 */2 * * *",
  },
  {
    id: "tech_review",
    name: "Tech review",
    description: "Architecture drift and design consistency review.",
    prompt:
      "Check architecture and API boundaries; flag drift, coupling, and migration risks.",
    defaultCron: "0 12 * * *",
  },
  {
    id: "qa_review",
    name: "QA review",
    description: "Test architecture, coverage, and flakiness signals.",
    prompt:
      "Review test architecture, coverage, and flakiness; report high-signal findings.",
    defaultCron: "0 15 * * *",
  },
  {
    id: "security_review",
    name: "Security review",
    description: "Security posture and dependency signal sweep.",
    prompt:
      "Scan dependencies and secrets policy; list actionable security follow-ups.",
    defaultCron: "0 6 * * *",
  },
];

/**
 * Routine IDs that are SDLC cadence lanes — specialists wearing routine
 * clothing in older configs. These never belong in the routines list;
 * the FE filters them so the picker / summary stay clean.
 *
 * Pre-canonical routine ids (daily_security_review / code_map /
 * scan_*) used to be hidden too. They are no longer hidden — the
 * operator should SEE drift from the canonical six so they can clean
 * up DB orphans by hand. Hiding them masks "I have stale pipeline rows
 * from an old seed" which is exactly the kind of mismatch the editor
 * needs to surface, not paper over.
 */
export const HIDDEN_ROUTINE_IDS: ReadonlySet<string> = new Set([
  "task_intake",
  "ba_requirements",
  "tech_arch_plan",
  "qa_arch_plan",
  "dev_implementation",
  "qa_manual",
  "qa_automation",
]);

/**
 * IDs the FE knows are routines but pre-date the canonical six. Used
 * to paint a "legacy" pill so the operator can spot drift.
 */
export const CANONICAL_ROUTINE_IDS: ReadonlySet<string> = new Set(
  BUILTIN_ROUTINE_CATALOG.map((entry) => entry.id),
);

export function isCanonicalRoutineId(id: string): boolean {
  return CANONICAL_ROUTINE_IDS.has(id);
}

export const CRON_PRESETS: { label: string; value: string }[] = [
  { label: "Every hour (top of hour, UTC)", value: "0 * * * *" },
  { label: "Every 2 hours (UTC)", value: "0 */2 * * *" },
  { label: "Weekdays 09:00, 13:00, 17:00 UTC", value: "0 9,13,17 * * 1-5" },
  { label: "Weekdays 09:00 UTC", value: "0 9 * * 1-5" },
  { label: "Daily 09:00 UTC", value: "0 9 * * *" },
  { label: "Mondays 09:00 UTC", value: "0 9 * * 1" },
  { label: "Monthly 1st 09:00 UTC", value: "0 9 1 * *" },
];
