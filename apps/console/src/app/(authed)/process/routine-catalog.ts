/**
 * Canonical routine catalog — seven routines, mirrored against backend
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
  {
    id: "process_review",
    name: "Process review",
    description:
      "Delivery patterns + SDLC suggestions to the inbox (PR previews, CI health, branch hygiene).",
    prompt:
      "Look at the last 7-30 days of repo activity; suggest concrete SDLC improvements as inbox items.",
    defaultCron: "0 16 * * *",
  },
];

/**
 * Routine IDs that are SDLC cadence lanes — specialists wearing routine
 * clothing in older configs. These never belong in the routines list;
 * the FE filters them so the picker / summary stay clean.
 *
 * The drift-orphan story changed: the backend projector now sources
 * routines exclusively from :class:`Lane` rows (kept in lockstep with
 * ``.ship/config.yml`` by ``lanes_sync``), so legacy ``Pipeline``
 * orphans never reach the FE in the first place. The "show drift,
 * clean by hand" design is gone; the page renders what's in the repo
 * and nothing else.
 */
export const HIDDEN_ROUTINE_IDS: ReadonlySet<string> = new Set([
  "task_intake",
  "bug_triage",
  "ba_requirements",
  "tech_arch_plan",
  "qa_arch_plan",
  "dev_implementation",
  "qa_manual",
  "qa_automation",
  "code_review",
]);

/**
 * IDs the FE knows are routines but pre-date the canonical seven. Used
 * to paint a "legacy" pill so the operator can spot drift on configs
 * that haven't been re-seeded to the current canon yet.
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
