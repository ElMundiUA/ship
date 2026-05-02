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
 * Routine IDs the FE hides at render time. Two groups:
 *
 *  - SDLC cadence lanes — they're *specialists*, never routines.
 *  - Pre-canonical seed ids — older repos may still carry them in
 *    ``.ship/config.yml`` until rewritten. The display layer hides
 *    them; the runtime keeps reading them so nothing breaks. Once a
 *    repo's config is rewritten to the canonical short ids, these
 *    legacy keys vanish on their own.
 */
export const HIDDEN_ROUTINE_IDS: ReadonlySet<string> = new Set([
  // SDLC cadence — specialists, not routines.
  "task_intake",
  "ba_requirements",
  "tech_arch_plan",
  "qa_arch_plan",
  "dev_implementation",
  "qa_manual",
  "qa_automation",
  // Pre-canonical seed ids (hidden so the operator only ever sees the
  // six new short labels). Mirrors lane_recipes.LEGACY_ROUTINE_IDS.
  "daily_security_review",
  "daily_digest",
  "daily_technical_architecture_review",
  "daily_architecture_tests_review",
  "daily_retro",
  "self_heal",
  "daily_standup",
  "tech_debt",
  "code_map",
  "flow_release_notes",
  "scan_docs_freshness",
  "scan_license_deps",
  "scan_security_deps",
]);

export const CRON_PRESETS: { label: string; value: string }[] = [
  { label: "Every hour (top of hour, UTC)", value: "0 * * * *" },
  { label: "Every 2 hours (UTC)", value: "0 */2 * * *" },
  { label: "Weekdays 09:00, 13:00, 17:00 UTC", value: "0 9,13,17 * * 1-5" },
  { label: "Weekdays 09:00 UTC", value: "0 9 * * 1-5" },
  { label: "Daily 09:00 UTC", value: "0 9 * * *" },
  { label: "Mondays 09:00 UTC", value: "0 9 * * 1" },
  { label: "Monthly 1st 09:00 UTC", value: "0 9 1 * *" },
];
