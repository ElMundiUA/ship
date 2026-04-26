/**
 * Preset routine ids aligned with backend projection and lane seeds (see processes._ROUTINE_IDS).
 */
export const BUILTIN_ROUTINE_CATALOG: {
  id: string;
  name: string;
  description: string;
  defaultCron: string;
}[] = [
  {
    id: "daily_architecture_tests_review",
    name: "Architecture tests review",
    description: "Recurring check on test architecture and coverage signals.",
    defaultCron: "0 8 * * 1-5",
  },
  {
    id: "daily_technical_architecture_review",
    name: "Technical architecture review",
    description: "Architecture drift and design consistency review.",
    defaultCron: "0 10 * * 1",
  },
  {
    id: "daily_security_review",
    name: "Security review",
    description: "Security posture and dependency signal sweep.",
    defaultCron: "0 6 * * *",
  },
  {
    id: "daily_digest",
    name: "Daily digest",
    description: "Consolidated summary of work and blockers for the team.",
    defaultCron: "0 8 * * 1-5",
  },
  {
    id: "daily_retro",
    name: "Daily retro",
    description: "Lightweight team retro prompts and follow-ups.",
    defaultCron: "0 16 * * 5",
  },
  {
    id: "self_heal",
    name: "Self heal",
    description: "Reconcile CI, workflows, and guardrails after failed runs.",
    defaultCron: "0 */2 * * *",
  },
  {
    id: "daily_standup",
    name: "Daily standup",
    description: "Asynchronous standup nudge with lane status.",
    defaultCron: "0 9 * * 1-5",
  },
  {
    id: "tech_debt",
    name: "Tech debt",
    description: "Triage and size technical-debt work for upcoming cycles.",
    defaultCron: "0 4 * * 0",
  },
];

export const CRON_PRESETS: { label: string; value: string }[] = [
  { label: "Every hour (top of hour, UTC)", value: "0 * * * *" },
  { label: "Every 2 hours (UTC)", value: "0 */2 * * *" },
  { label: "Weekdays 09:00, 13:00, 17:00 UTC", value: "0 9,13,17 * * 1-5" },
  { label: "Weekdays 09:00 UTC", value: "0 9 * * 1-5" },
  { label: "Daily 09:00 UTC", value: "0 9 * * *" },
  { label: "Mondays 09:00 UTC", value: "0 9 * * 1" },
  { label: "Monthly 1st 09:00 UTC", value: "0 9 1 * *" },
];
