/**
 * Canonical routine catalog — six routines, mirrored against backend
 * ``DEFAULT_SEED_LANES`` in ``backend/app/services/lane_recipes.py``.
 *
 * The IDs are the runtime identifiers that ``shipctl`` reads out of
 * ``.ship/config.yml``; they MUST stay byte-stable so existing repos
 * keep working when we re-render this list. Display labels (the short
 * "Daily / Retro / Healthcheck" form) are FE-only — the YAML stays
 * verbose (``daily_security_review`` etc.) but the picker shows the
 * short label so the operator isn't reading "daily_architecture_tests_
 * review" out of a dropdown.
 *
 * SDLC cadence lanes (``task_intake``, ``ba_requirements``,
 * ``tech_arch_plan``, ``qa_arch_plan``) are deliberately excluded.
 * Those are *specialists* on the process, not user-facing routines —
 * they show up under Capacity and the Flow editor instead.
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
    id: "daily_digest",
    name: "Daily",
    description: "Consolidated summary of work and blockers for the team.",
    prompt: "Summarize in-flight work, blockers, and risks for the team.",
    defaultCron: "0 9 * * *",
  },
  {
    id: "daily_retro",
    name: "Retro",
    description: "Lightweight retro: what went well, what to improve, next actions.",
    prompt: "Run a short retro: what went well, what to improve, next actions.",
    defaultCron: "0 18 * * *",
  },
  {
    id: "self_heal",
    name: "Healthcheck",
    description: "Reconcile CI, workflows, and guardrails after failed runs.",
    prompt:
      "Reconcile CI, workflows, and guardrails; open minimal fixes for broken gates.",
    defaultCron: "0 */2 * * *",
  },
  {
    id: "daily_technical_architecture_review",
    name: "Tech review",
    description: "Architecture drift and design consistency review.",
    prompt:
      "Check architecture and API boundaries; flag drift, coupling, and migration risks.",
    defaultCron: "0 12 * * *",
  },
  {
    id: "daily_architecture_tests_review",
    name: "QA review",
    description: "Test architecture, coverage, and flakiness signals.",
    prompt:
      "Review test architecture, coverage, and flakiness; report high-signal findings.",
    defaultCron: "0 15 * * *",
  },
  {
    id: "daily_security_review",
    name: "Security review",
    description: "Security posture and dependency signal sweep.",
    prompt:
      "Scan dependencies and secrets policy; list actionable security follow-ups.",
    defaultCron: "0 6 * * *",
  },
];

/**
 * Routine IDs that the FE intentionally hides — they were seeded into
 * older repos but are either deprecated, system-only, or moved to a
 * different surface. The routines panel filters them out of view; they
 * stay live in YAML so the runtime keeps treating them normally.
 *
 * - SDLC cadence lanes (``task_intake``…``qa_arch_plan``) are
 *   specialists, not routines — see Capacity / Flow.
 * - ``daily_standup`` and ``tech_debt`` were experimental, never made
 *   the canonical six.
 */
export const HIDDEN_ROUTINE_IDS: ReadonlySet<string> = new Set([
  "task_intake",
  "ba_requirements",
  "tech_arch_plan",
  "qa_arch_plan",
  "daily_standup",
  "tech_debt",
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
