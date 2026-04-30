/**
 * Mock data for the Ship console UI.
 *
 * Lives entirely in the browser/server bundle — no DB, no fetch. Once the
 * v1 API stabilises we swap these readers for real `fetch('/v1/...')`
 * without touching the components that consume them.
 */

if (typeof process !== "undefined" && process.env.NEXT_PUBLIC_USE_MOCK !== "1") {
  throw new Error(
    "console/src/lib/mock/cloud.ts must not be imported in production. Set NEXT_PUBLIC_USE_MOCK=1 to enable for local UI/Storybook work.",
  );
}

/** Treat "now" as fixed at module load so SSR + client render match. */
const NOW = new Date();
function ago(opts: { minutes?: number; hours?: number; days?: number }): string {
  const ms =
    (opts.minutes ?? 0) * 60_000 +
    (opts.hours ?? 0) * 3_600_000 +
    (opts.days ?? 0) * 86_400_000;
  return new Date(NOW.getTime() - ms).toISOString();
}
function isoDay(daysAgo: number): string {
  return new Date(NOW.getTime() - daysAgo * 86_400_000).toISOString().slice(0, 10);
}

export type ArtifactSource = "global" | "workspace" | "project";
export type ArtifactKind = "pattern" | "tool" | "collection";

export type WorkspaceMock = {
  id: string;
  slug: string;
  name: string;
  org: string;
  plan: "free" | "team" | "enterprise";
  members: number;
  catalogSources: Record<ArtifactSource, boolean>;
};

export const workspaces: WorkspaceMock[] = [
  {
    id: "ws_helio_core",
    slug: "helio-core",
    name: "Helio · Platform",
    org: "Helio Labs",
    plan: "team",
    members: 14,
    catalogSources: { global: true, workspace: true, project: true },
  },
  {
    id: "ws_helio_pay",
    slug: "helio-pay",
    name: "Helio · Payments",
    org: "Helio Labs",
    plan: "team",
    members: 9,
    catalogSources: { global: true, workspace: true, project: true },
  },
  {
    id: "ws_solo",
    slug: "personal",
    name: "Personal",
    org: "denis@helio.dev",
    plan: "free",
    members: 1,
    catalogSources: { global: true, workspace: true, project: true },
  },
];

export const currentWorkspaceId = workspaces[0].id;
export const currentUser = {
  name: "Denis K.",
  email: "denis@helio.dev",
  avatarInitials: "DK",
  role: "owner" as const,
};

export type ArtifactRow = {
  id: string;
  kind: ArtifactKind;
  name: string;
  summary: string;
  version: string;
  channel: "stable" | "beta" | "alpha";
  authors: string[];
  source: ArtifactSource;
  /** Set when the workspace/project layer overrides a global entry of the same id. */
  overrides?: ArtifactSource;
  updatedAt: string;
  tags: string[];
  group: string;
};

export const artifacts: ArtifactRow[] = [
  {
    id: "onboard-adopt",
    kind: "pattern",
    name: "Adopt Ship — generic baseline",
    summary:
      "Wires a fresh repo into the Ship cadence: queues, daily/retro, QA-first habits.",
    version: "1.4.0",
    channel: "stable",
    authors: ["@ship/core"],
    source: "global",
    updatedAt: ago({ days: 7 }),
    tags: ["onboarding", "baseline"],
    group: "onboarding",
  },
  {
    id: "onboard-adopt",
    kind: "pattern",
    name: "Adopt Ship — Helio override",
    summary: "Helio-flavoured queue names + on-call routing baked in.",
    version: "1.4.0+helio.3",
    channel: "stable",
    authors: ["@helio/platform"],
    source: "workspace",
    overrides: "global",
    updatedAt: ago({ hours: 14 }),
    tags: ["onboarding", "helio"],
    group: "onboarding",
  },
  {
    id: "pr-and-ci-gate",
    kind: "pattern",
    name: "PR + CI gate",
    summary:
      "Block merge until checks green and reviewer assigned; opens an Action Item if a bypass happens.",
    version: "0.7.2",
    channel: "stable",
    authors: ["@ship/core"],
    source: "global",
    updatedAt: ago({ days: 11 }),
    tags: ["release", "ci"],
    group: "release",
  },
  {
    id: "scheduled-sdlc-lane",
    kind: "pattern",
    name: "Scheduled SDLC lane",
    summary: "Runs the daily + retro lanes on cron and posts the digest to Slack.",
    version: "0.4.0",
    channel: "stable",
    authors: ["@ship/core"],
    source: "global",
    updatedAt: ago({ days: 4 }),
    tags: ["scheduled", "daily", "retro"],
    group: "cadence",
  },
  {
    id: "linear",
    kind: "tool",
    name: "Linear tracker",
    summary: "Read/write tracker tasks; honours Ship's project-state contract.",
    version: "1.2.1",
    channel: "stable",
    authors: ["@ship/core"],
    source: "global",
    updatedAt: ago({ days: 16 }),
    tags: ["tracker", "linear"],
    group: "tools",
  },
  {
    id: "snyk",
    kind: "tool",
    name: "Snyk scan",
    summary: "Wraps the Snyk CLI for vuln + license findings during CI gate.",
    version: "0.9.0",
    channel: "beta",
    authors: ["@ship/core"],
    source: "global",
    updatedAt: ago({ days: 21 }),
    tags: ["security", "ci"],
    group: "tools",
  },
  {
    id: "helio-payments-runbook",
    kind: "pattern",
    name: "Helio Payments runbook",
    summary:
      "Internal-only: PSP fail-over checklist, on-call rotation, customer-comms macros.",
    version: "0.3.0",
    channel: "stable",
    authors: ["@helio/platform"],
    source: "workspace",
    updatedAt: ago({ days: 2 }),
    tags: ["runbook", "payments", "internal"],
    group: "ops",
  },
  {
    id: "helio-design-tokens",
    kind: "tool",
    name: "Helio design tokens",
    summary:
      "Project-pinned wrapper around our DSP exporter; replaces `tokens-cli` in just this project.",
    version: "2.0.0",
    channel: "stable",
    authors: ["@helio/design-systems"],
    source: "project",
    updatedAt: ago({ hours: 3 }),
    tags: ["design", "tokens"],
    group: "design",
  },
];

export type PullRequestRow = {
  id: string;
  number: number;
  title: string;
  author: string;
  authorAvatarInitials: string;
  artifactId: string;
  artifactKind: ArtifactKind;
  diffSummary: { added: number; removed: number; files: number };
  status: "open" | "needs-review" | "ready" | "blocked";
  ci: "passing" | "failing" | "pending";
  openedAt: string;
  changeKind: "minor" | "major" | "patch";
  description: string;
};

export const pullRequests: PullRequestRow[] = [
  {
    id: "pr_421",
    number: 421,
    title: "pattern: tighten daily-digest summary template",
    author: "Mira Tan",
    authorAvatarInitials: "MT",
    artifactId: "daily-digest",
    artifactKind: "pattern",
    diffSummary: { added: 18, removed: 9, files: 1 },
    status: "ready",
    ci: "passing",
    openedAt: ago({ hours: 13 }),
    changeKind: "minor",
    description:
      "Caps each section to 5 lines and adds a 'top blocker' bullet so scrum bots can pin it.",
  },
  {
    id: "pr_419",
    number: 419,
    title: "tool(snyk): bump container scanner; add Helio-allowlist",
    author: "Jordan Lee",
    authorAvatarInitials: "JL",
    artifactId: "snyk",
    artifactKind: "tool",
    diffSummary: { added: 56, removed: 12, files: 4 },
    status: "needs-review",
    ci: "passing",
    openedAt: ago({ hours: 21 }),
    changeKind: "minor",
    description:
      "Allowlists three third-party CVEs that don't apply to the Helio runtime image; container scan now succeeds in <30s.",
  },
  {
    id: "pr_418",
    number: 418,
    title: "workflow: pipeline-self-heal — auto-restart only on transient errors",
    author: "Sam Chen",
    authorAvatarInitials: "SC",
    artifactId: "pipeline-self-heal",
    artifactKind: "pattern",
    diffSummary: { added: 31, removed: 14, files: 2 },
    status: "open",
    ci: "failing",
    openedAt: ago({ days: 1, hours: 8 }),
    changeKind: "major",
    description:
      "Adds a transient-error allow-list (network/timeouts) and refuses to retry on logical failures. CI flagged a missing test for the 5xx path.",
  },
  {
    id: "pr_417",
    number: 417,
    title: "collection: agent-rules-cursor — Cursor 1.5 prompt updates",
    author: "Riley Park",
    authorAvatarInitials: "RP",
    artifactId: "agent-rules-cursor",
    artifactKind: "collection",
    diffSummary: { added: 124, removed: 88, files: 7 },
    status: "blocked",
    ci: "pending",
    openedAt: ago({ days: 1, hours: 21 }),
    changeKind: "major",
    description:
      "New Cursor 1.5 default rules. Blocked: needs sign-off from owner of the Cursor collection in /global.",
  },
];

export type KnowledgeBucket = {
  id: string;
  name: string;
  summary: string;
  documents: number;
  totalBytes: number;
  embeddings: number;
  status: "ready" | "indexing" | "error";
  visibility: "workspace" | "project" | "private";
  updatedAt: string;
  glyph: string;
};

export const knowledgeBuckets: KnowledgeBucket[] = [
  {
    id: "kb_devops",
    name: "DevOps rules · Helio",
    summary:
      "Org-wide DevOps standards: IaC patterns, on-call rotations, postmortem template.",
    documents: 38,
    totalBytes: 78 * 1024 * 1024,
    embeddings: 1842,
    status: "ready",
    visibility: "workspace",
    updatedAt: ago({ hours: 14 }),
    glyph: "devops",
  },
  {
    id: "kb_security",
    name: "Security policies",
    summary:
      "ISO 27001 controls, threat model, third-party SOC2 reports, secrets handling.",
    documents: 22,
    totalBytes: 34 * 1024 * 1024,
    embeddings: 921,
    status: "ready",
    visibility: "workspace",
    updatedAt: ago({ days: 4 }),
    glyph: "security",
  },
  {
    id: "kb_pay_design",
    name: "Payments · design specs",
    summary: "Pixel specs, Figma exports, PSP UX research, copy guidelines.",
    documents: 47,
    totalBytes: 312 * 1024 * 1024,
    embeddings: 2104,
    status: "indexing",
    visibility: "project",
    updatedAt: ago({ minutes: 18 }),
    glyph: "design",
  },
  {
    id: "kb_compliance",
    name: "Compliance · GDPR + DORA",
    summary: "Regulator-facing docs, DPIAs, vendor risk assessments.",
    documents: 15,
    totalBytes: 22 * 1024 * 1024,
    embeddings: 612,
    status: "error",
    visibility: "workspace",
    updatedAt: ago({ days: 7 }),
    glyph: "compliance",
  },
];

export type KnowledgeDoc = {
  id: string;
  bucketId: string;
  name: string;
  type: "pdf" | "docx" | "pptx" | "md" | "html" | "csv";
  size: number;
  pages: number;
  uploadedBy: string;
  uploadedAt: string;
  status: "ready" | "parsing" | "embedding" | "failed";
  chunks: number;
};

export const knowledgeDocs: KnowledgeDoc[] = [
  {
    id: "doc_1",
    bucketId: "kb_devops",
    name: "On-call runbook v3.pptx",
    type: "pptx",
    size: 4_200_000,
    pages: 28,
    uploadedBy: "Mira Tan",
    uploadedAt: ago({ hours: 14 }),
    status: "ready",
    chunks: 124,
  },
  {
    id: "doc_2",
    bucketId: "kb_devops",
    name: "Terraform standards.md",
    type: "md",
    size: 88_000,
    pages: 14,
    uploadedBy: "Sam Chen",
    uploadedAt: ago({ days: 1, hours: 22 }),
    status: "ready",
    chunks: 41,
  },
  {
    id: "doc_3",
    bucketId: "kb_devops",
    name: "K8s upgrade plan Q2.pdf",
    type: "pdf",
    size: 1_800_000,
    pages: 22,
    uploadedBy: "Jordan Lee",
    uploadedAt: ago({ minutes: 28 }),
    status: "embedding",
    chunks: 0,
  },
  {
    id: "doc_4",
    bucketId: "kb_devops",
    name: "Postmortem template.docx",
    type: "docx",
    size: 240_000,
    pages: 6,
    uploadedBy: "Riley Park",
    uploadedAt: ago({ days: 4 }),
    status: "ready",
    chunks: 18,
  },
  {
    id: "doc_5",
    bucketId: "kb_devops",
    name: "Old SRE handbook.pdf",
    type: "pdf",
    size: 12_400_000,
    pages: 184,
    uploadedBy: "Denis K.",
    uploadedAt: ago({ days: 9 }),
    status: "failed",
    chunks: 0,
  },
];

// --- Dashboard / effectiveness ---

export type Kpi = {
  label: string;
  value: string;
  delta?: { sign: "up" | "down" | "flat"; pct: number };
  hint: string;
};

export const kpis: Kpi[] = [
  {
    label: "Lead time (PR→prod)",
    value: "2.4 d",
    delta: { sign: "down", pct: 18 },
    hint: "down = better; vs prior 14 days",
  },
  {
    label: "Daily lane success",
    value: "94%",
    delta: { sign: "up", pct: 6 },
    hint: "% of mornings the digest landed cleanly",
  },
  {
    label: "Retro action follow-through",
    value: "78%",
    delta: { sign: "up", pct: 11 },
    hint: "approved retro items closed within 7 d",
  },
  {
    label: "Catalog freshness",
    value: "12 stale",
    delta: { sign: "down", pct: 23 },
    hint: "patterns/tools not updated in 90 d",
  },
];

export type LaneRun = {
  id: string;
  kind: "daily" | "retro" | "scheduled" | "self-heal";
  startedAt: string;
  durationSec: number;
  status: "ok" | "warning" | "failed";
  trigger: string;
  highlight: string;
};

export const recentRuns: LaneRun[] = [
  {
    id: "run_980",
    kind: "daily",
    startedAt: ago({ minutes: 38 }),
    durationSec: 38,
    status: "ok",
    trigger: "cron · 07:00 UTC",
    highlight: "9 changes summarised, 2 reviewers nudged",
  },
  {
    id: "run_979",
    kind: "self-heal",
    startedAt: ago({ hours: 2, minutes: 14 }),
    durationSec: 96,
    status: "warning",
    trigger: "CI red on `payments-api`",
    highlight: "Restarted flaky test job; opened action item #482",
  },
  {
    id: "run_978",
    kind: "retro",
    startedAt: ago({ hours: 9, minutes: 30 }),
    durationSec: 71,
    status: "ok",
    trigger: "cron · friday 17:30",
    highlight: "5 action items proposed, 3 approved",
  },
  {
    id: "run_977",
    kind: "scheduled",
    startedAt: ago({ hours: 19 }),
    durationSec: 22,
    status: "ok",
    trigger: "scheduled-sdlc-lane",
    highlight: "Verified CHANGELOGs across 11 services",
  },
  {
    id: "run_976",
    kind: "daily",
    startedAt: ago({ days: 1, hours: 1 }),
    durationSec: 142,
    status: "failed",
    trigger: "cron · 07:00 UTC",
    highlight: "Linear API throttled; report missed; auto-retry queued",
  },
];

export type ActionItem = {
  id: string;
  title: string;
  reason: string;
  source: "daily" | "retro" | "self-heal" | "ci";
  proposedAt: string;
  proposedBy: string;
  severity: "low" | "med" | "high";
  estimate: string;
  status: "proposed" | "approved" | "rejected" | "in-tracker";
  trackerKey?: string;
};

export const actionItems: ActionItem[] = [
  {
    id: "ai_482",
    title: "Quarantine `payments-api` integration suite",
    reason:
      "Self-heal saw 3 transient PSP timeouts in 24h. Move suite to nightly until PSP rate-limit is renegotiated.",
    source: "self-heal",
    proposedAt: ago({ hours: 2, minutes: 14 }),
    proposedBy: "self-heal lane",
    severity: "high",
    estimate: "S · ~2h",
    status: "proposed",
  },
  {
    id: "ai_481",
    title: "Pin `agent-rules-cursor` to v1.5 in personal workspace",
    reason: "Daily found 2 contributors still on Cursor 1.4 prompts.",
    source: "daily",
    proposedAt: ago({ minutes: 38 }),
    proposedBy: "daily lane",
    severity: "low",
    estimate: "XS · ~15m",
    status: "proposed",
  },
  {
    id: "ai_478",
    title: "Document on-call ramp-down for new joiner Mira",
    reason: "Retro flagged that Mira shadowed only 1 incident before primary rotation.",
    source: "retro",
    proposedAt: ago({ hours: 9, minutes: 30 }),
    proposedBy: "retro lane",
    severity: "med",
    estimate: "M · ~1d",
    status: "approved",
    trackerKey: "HEL-2104",
  },
  {
    id: "ai_477",
    title: "Sunset `legacy-billing-job` after Q2 close",
    reason: "Retro: job hasn't run in 38 days; keeping the alert costs 11h/mo of triage.",
    source: "retro",
    proposedAt: ago({ hours: 9, minutes: 28 }),
    proposedBy: "retro lane",
    severity: "med",
    estimate: "S · ~3h",
    status: "approved",
    trackerKey: "HEL-2105",
  },
  {
    id: "ai_476",
    title: "Move flaky `auth.cookie` test to skip-list",
    reason: "CI flagged 6 reds in 2 days; root cause is a known clock-skew bug.",
    source: "ci",
    proposedAt: ago({ days: 1, hours: 8 }),
    proposedBy: "ci-watch lane",
    severity: "low",
    estimate: "XS · ~30m",
    status: "rejected",
  },
];

export type DailyDigest = {
  date: string;
  summary: string;
  shipped: string[];
  inFlight: string[];
  blockers: string[];
};

export const yesterdayDigest: DailyDigest = {
  date: isoDay(1),
  summary:
    "Quiet day. Payments shipped the PSP fail-over toggle, Platform unblocked the Cursor 1.5 rules update PR. One retro flagged that the on-call ramp-down for new joiners is still informal — see action items.",
  shipped: [
    "Payments · PSP fail-over toggle behind feature flag",
    "Platform · `pr-and-ci-gate` 0.7.2 rolled out org-wide",
    "Design · Helio tokens 2.0 cut, project workspace switched over",
  ],
  inFlight: [
    "Snyk scanner upgrade — review pending from @jordan",
    "Pipeline self-heal v0.4 — failing CI for 5xx path",
  ],
  blockers: [
    "Cursor 1.5 rules collection — needs sign-off from /global owner",
  ],
};

// --- Telemetry charts ---

export type TelemetryDay = { day: string; events: number; success: number };

export const telemetrySeries: TelemetryDay[] = [
  { day: "Mon", events: 412, success: 392 },
  { day: "Tue", events: 488, success: 472 },
  { day: "Wed", events: 521, success: 498 },
  { day: "Thu", events: 470, success: 460 },
  { day: "Fri", events: 553, success: 549 },
  { day: "Sat", events: 134, success: 132 },
  { day: "Sun", events: 96, success: 95 },
];

export type TelemetryEvent = {
  id: string;
  ts: string;
  kind: string;
  actor: string;
  object: string;
  result: "ok" | "warn" | "err";
};

export const telemetryEvents: TelemetryEvent[] = [
  { id: "ev_1", ts: ago({ minutes: 2 }),  kind: "shipctl.pattern.fetch", actor: "denis@helio.dev",      object: "pattern/onboard-adopt@1.4.0", result: "ok"   },
  { id: "ev_2", ts: ago({ minutes: 5 }),  kind: "workflow.run",          actor: "scheduled-sdlc-lane",  object: "lane/daily",                       result: "ok"   },
  { id: "ev_3", ts: ago({ minutes: 8 }),  kind: "shipctl.pattern.search",actor: "mira@helio.dev",       object: "query='on-call rotation'",         result: "ok"   },
  { id: "ev_4", ts: ago({ minutes: 14 }), kind: "knowledge.embed",       actor: "kb_pay_design/uploads",object: "doc_pay_specs.pptx",               result: "warn" },
  { id: "ev_5", ts: ago({ minutes: 22 }), kind: "tracker.create",        actor: "retro lane",           object: "linear:HEL-2105",                  result: "ok"   },
  { id: "ev_6", ts: ago({ minutes: 41 }), kind: "shipctl.tool.fetch",    actor: "ci",                   object: "tool/snyk@0.9.0",                  result: "err"  },
];

export type Integration = {
  id: string;
  kind: "linear" | "github" | "slack" | "otel" | "webhook" | "s3";
  label: string;
  status: "connected" | "warning" | "off";
  detail: string;
};

export const integrations: Integration[] = [
  {
    id: "int_linear",
    kind: "linear",
    label: "Linear · HelioLabs",
    status: "connected",
    detail: "writes approved retro items as `HEL-` issues",
  },
  {
    id: "int_github",
    kind: "github",
    label: "GitHub · helio/* repos",
    status: "connected",
    detail: "PR review handles for catalog merges",
  },
  {
    id: "int_slack",
    kind: "slack",
    label: "Slack · #ship-daily",
    status: "connected",
    detail: "daily digest posted at 07:05 UTC",
  },
  {
    id: "int_otel",
    kind: "otel",
    label: "OpenTelemetry · Honeycomb",
    status: "warning",
    detail: "exporter 4xx since Apr 17 — refresh API key",
  },
  {
    id: "int_webhook",
    kind: "webhook",
    label: "Custom webhook · Pagerduty",
    status: "off",
    detail: "fires on action_item.severity=high",
  },
  {
    id: "int_s3",
    kind: "s3",
    label: "S3 · helio-ship-events",
    status: "connected",
    detail: "JSONL export, hourly rotation",
  },
];

// --- artifact detail (versions + readme) ---

export type ArtifactVersion = {
  version: string;
  channel: "stable" | "beta" | "alpha";
  releasedAt: string;
  releasedBy: string;
  notes: string;
  diffStat: { added: number; removed: number; files: number };
};

export const artifactVersions: Record<string, ArtifactVersion[]> = {
  "onboard-adopt": [
    {
      version: "1.4.0+helio.3",
      channel: "stable",
      releasedAt: ago({ hours: 14 }),
      releasedBy: "@helio/platform",
      notes: "Routes the on-call rotation to PagerDuty `helio-platform` and renames daily-digest queue.",
      diffStat: { added: 22, removed: 7, files: 2 },
    },
    {
      version: "1.4.0",
      channel: "stable",
      releasedAt: ago({ days: 7 }),
      releasedBy: "@ship/core",
      notes: "Daily-digest template tightened to 5 bullets per section + top-blocker line.",
      diffStat: { added: 18, removed: 9, files: 1 },
    },
    {
      version: "1.3.2",
      channel: "stable",
      releasedAt: ago({ days: 23 }),
      releasedBy: "@ship/core",
      notes: "Patch: workflow `pr-and-ci-gate` now optional in adopt step.",
      diffStat: { added: 4, removed: 2, files: 1 },
    },
    {
      version: "1.3.0",
      channel: "stable",
      releasedAt: ago({ days: 41 }),
      releasedBy: "@ship/core",
      notes: "Adds knowledge-bucket bootstrap step.",
      diffStat: { added: 56, removed: 12, files: 4 },
    },
  ],
};

export type ArtifactReadme = {
  intro: string;
  usage: string;
  inputs: { name: string; required: boolean; default?: string; help: string }[];
  outputs: string[];
  rationale: string;
};

export const artifactReadmes: Record<string, ArtifactReadme> = {
  "onboard-adopt": {
    intro:
      "Wires a fresh repo into the Ship cadence. Installs the daily + retro lanes, opens the project-state queues, and lays down the agent rules collection your project's stack expects.",
    usage:
      "shipctl pattern apply onboard-adopt --workspace <slug> --project <id>",
    inputs: [
      { name: "tracker", required: true, help: "Tracker provider for action items: linear | jira | github" },
      { name: "slack_channel", required: false, default: "#ship-daily", help: "Daily-digest target" },
      { name: "agent_stack", required: false, default: "cursor", help: "cursor | claude | continue | aider" },
    ],
    outputs: [
      ".ship/lanes/daily.yaml",
      ".ship/lanes/retro.yaml",
      ".ship/queues/action-items.md",
      ".ship/agent-rules/<stack>/",
    ],
    rationale:
      "Apply this on day 1 of any new project. Once installed, the daily lane keeps yesterday visible and the retro lane keeps the team honest. Override locally by registering a workspace artifact repo with the same id.",
  },
};

// --- knowledge bucket detail ---

export type KnowledgeChunk = {
  id: string;
  docId: string;
  docName: string;
  page: number;
  excerpt: string;
  score: number;
};

export const knowledgeChunks: KnowledgeChunk[] = [
  {
    id: "ck_1",
    docId: "doc_1",
    docName: "On-call runbook v3.pptx",
    page: 12,
    score: 0.91,
    excerpt:
      "Severity-1 alert routing: Pager goes to the primary on-call. Customer comms macro `psp-fail-over` lives in `runbooks/comms/`. Update the status page within 5 minutes.",
  },
  {
    id: "ck_2",
    docId: "doc_2",
    docName: "Terraform standards.md",
    page: 4,
    score: 0.87,
    excerpt:
      "All modules must declare `var.environment` and pin provider versions. The CI lane runs `terraform validate` + `tflint` against every PR; failures block merge.",
  },
  {
    id: "ck_3",
    docId: "doc_4",
    docName: "Postmortem template.docx",
    page: 1,
    score: 0.83,
    excerpt:
      "Postmortem must answer four questions: what was the impact, what was the root cause, what is the fix, what is the prevention. Owner is the on-call lead at the time of incident.",
  },
  {
    id: "ck_4",
    docId: "doc_1",
    docName: "On-call runbook v3.pptx",
    page: 7,
    score: 0.79,
    excerpt:
      "Rotation cadence: weekly hand-off Mondays 10:00 UTC. New joiners must shadow at least 2 incidents before primary rotation. Track via the retro lane.",
  },
  {
    id: "ck_5",
    docId: "doc_2",
    docName: "Terraform standards.md",
    page: 9,
    score: 0.74,
    excerpt:
      "Secrets never live in `*.tfvars`. Use the workspace SOPS key, encrypted at rest, decrypted only inside the apply container. Ship's audit log captures every decrypt.",
  },
];

// --- effectiveness deep-dive ---

export type EffectivenessWeek = {
  weekLabel: string;
  leadTimeDays: number;
  throughputPRs: number;
  mttrHours: number;
  retroFollowThroughPct: number;
};

export const effectivenessWeeks: EffectivenessWeek[] = [
  { weekLabel: "Wk-12", leadTimeDays: 4.1, throughputPRs: 18, mttrHours: 6.4, retroFollowThroughPct: 41 },
  { weekLabel: "Wk-11", leadTimeDays: 3.9, throughputPRs: 21, mttrHours: 5.8, retroFollowThroughPct: 48 },
  { weekLabel: "Wk-10", leadTimeDays: 3.6, throughputPRs: 22, mttrHours: 4.7, retroFollowThroughPct: 52 },
  { weekLabel: "Wk-9",  leadTimeDays: 3.4, throughputPRs: 24, mttrHours: 5.2, retroFollowThroughPct: 58 },
  { weekLabel: "Wk-8",  leadTimeDays: 3.1, throughputPRs: 27, mttrHours: 4.4, retroFollowThroughPct: 60 },
  { weekLabel: "Wk-7",  leadTimeDays: 2.9, throughputPRs: 26, mttrHours: 3.9, retroFollowThroughPct: 64 },
  { weekLabel: "Wk-6",  leadTimeDays: 2.8, throughputPRs: 30, mttrHours: 4.1, retroFollowThroughPct: 68 },
  { weekLabel: "Wk-5",  leadTimeDays: 2.7, throughputPRs: 32, mttrHours: 3.6, retroFollowThroughPct: 71 },
  { weekLabel: "Wk-4",  leadTimeDays: 2.6, throughputPRs: 33, mttrHours: 3.2, retroFollowThroughPct: 73 },
  { weekLabel: "Wk-3",  leadTimeDays: 2.5, throughputPRs: 35, mttrHours: 3.0, retroFollowThroughPct: 76 },
  { weekLabel: "Wk-2",  leadTimeDays: 2.5, throughputPRs: 34, mttrHours: 2.8, retroFollowThroughPct: 77 },
  { weekLabel: "Wk-1",  leadTimeDays: 2.4, throughputPRs: 36, mttrHours: 2.6, retroFollowThroughPct: 78 },
];

export type AdoptionByPattern = {
  pattern: string;
  installedIn: number;
  totalProjects: number;
};

export const adoptionByPattern: AdoptionByPattern[] = [
  { pattern: "onboard-adopt",    installedIn: 11, totalProjects: 12 },
  { pattern: "pr-and-ci-gate",        installedIn: 12, totalProjects: 12 },
  { pattern: "scheduled-sdlc-lane",   installedIn:  9, totalProjects: 12 },
  { pattern: "agent-rules-cursor",    installedIn:  8, totalProjects: 12 },
  { pattern: "pipeline-self-heal",    installedIn:  6, totalProjects: 12 },
  { pattern: "hosted-e2e-regression", installedIn:  4, totalProjects: 12 },
];

// --- helpers ---
// ``formatBytes`` and ``relativeTime`` moved to ``@/lib/format`` so production
// routes can import them without tripping this module's import-time guard
// (which keeps mock data out of prod bundles).
