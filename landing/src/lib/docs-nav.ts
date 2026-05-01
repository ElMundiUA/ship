/**
 * Docs sidebar nav, grouped by part of the user manual. Used by docs/layout.tsx
 * and the /docs landing page card grid so labels and groupings can never drift.
 */

export type DocsNavItem = {
  href: string;
  label: string;
  blurb: string;
};

export type DocsNavGroup = {
  label: string;
  accent: "aqua" | "lilac" | "sun" | "coral";
  items: DocsNavItem[];
};

export const DOCS_NAV: DocsNavGroup[] = [
  {
    label: "Orientation",
    accent: "aqua",
    items: [
      {
        href: "/docs/orientation/what-is-ship",
        label: "What Ship is",
        blurb:
          "A workspace for AI-assisted product delivery — humans own intent, machines act inside fences, every action leaves a trail.",
      },
      {
        href: "/docs/orientation/vocabulary",
        label: "Vocabulary",
        blurb:
          "The seven words you'll meet on every screen: workspace, connected repo, tracker, Inbox, knowledge, process, evidence.",
      },
      {
        href: "/docs/orientation/a-day-in-ship",
        label: "A day in Ship",
        blurb: "What a normal morning looks like — open the console, drain the Inbox, glance at shipped, top up knowledge.",
      },
    ],
  },
  {
    label: "Setup",
    accent: "lilac",
    items: [
      {
        href: "/getting-started",
        label: "Quick checklist",
        blurb: "Short setup checklist for the operator who has done this before. Cross-links into the chapters below.",
      },
      {
        href: "/docs/setup/onboarding-wizard",
        label: "The onboarding wizard",
        blurb: "Walk the four wizard steps end-to-end: Install GitHub App → Pick repos → Workspace tracker → Confirm.",
      },
      {
        href: "/docs/setup/github-app",
        label: "GitHub App and repo activation",
        blurb: "Why an App and not a token; what the App can see; the two-layer model of install scope and activation.",
      },
      {
        href: "/docs/setup/tracker-binding",
        label: "Binding the tracker",
        blurb: "One tracker per workspace. Linear, Jira, GitHub Issues, GitLab, Azure DevOps — credentials and trade-offs.",
      },
      {
        href: "/docs/setup/members-and-roles",
        label: "Members and roles",
        blurb: "Three roles — owner, admin, member — and the last-owner protection that saves you a support ticket.",
      },
      {
        href: "/docs/setup/integrations",
        label: "Integrations beyond the tracker",
        blurb: "Notion, Slack, Teams, OpenTelemetry, S3 export, custom webhook — what each is for and how to wire it.",
      },
    ],
  },
  {
    label: "Knowledge",
    accent: "sun",
    items: [
      {
        href: "/docs/knowledge/overview",
        label: "What knowledge is for",
        blurb: "Why short articles age better than handbooks; what belongs in knowledge and what doesn't.",
      },
      {
        href: "/docs/knowledge/buckets",
        label: "Buckets",
        blurb: "Buckets as the unit of grouping. Workspace / project / repo / user scopes. When to split, when to merge.",
      },
      {
        href: "/docs/knowledge/importing",
        label: "Importing knowledge",
        blurb: "Sources — repo `.ship/knowledge`, website (Firecrawl), Notion, Confluence, docs repo, uploaded files.",
      },
      {
        href: "/docs/knowledge/distiller-and-review",
        label: "The distiller and the review path",
        blurb: "Nothing publishes silently — every imported note flows through routing, synthesis, and human review.",
      },
      {
        href: "/docs/knowledge/chat-as-knowledge",
        label: "Chat as a knowledge tool",
        blurb: "What the workspace chat is for, and how to save a clean thread as a knowledge article.",
      },
    ],
  },
  {
    label: "Inbox",
    accent: "coral",
    items: [
      {
        href: "/docs/inbox/overview",
        label: "Decision work, not notifications",
        blurb: "What the Inbox is, what it carries, and the rule that anything without a decision belongs elsewhere.",
      },
      {
        href: "/docs/inbox/item-types",
        label: "The five item types",
        blurb: "Clarification, improvement, approval, failure, exception — with the canonical action for each.",
      },
      {
        href: "/docs/inbox/routing-rules",
        label: "Routing rules",
        blurb: "Handles → user, group, or strategy. Configuration health: bound, used, orphaned, unbound.",
      },
      {
        href: "/docs/inbox/disposition",
        label: "Disposition",
        blurb: "The action vocabulary: answer, accept, approve, reject, retry, acknowledge, snooze, dismiss, reassign.",
      },
    ],
  },
  {
    label: "Process",
    accent: "aqua",
    items: [
      {
        href: "/docs/process/overview",
        label: "The model",
        blurb: "Process, states, routines, specialists — the three named pieces and how they fit together.",
      },
      {
        href: "/docs/process/editor",
        label: "Reading the process editor",
        blurb: "States, transitions, routines panel, tracker mapping, flow schedule — the panels of `/process/<id>`.",
      },
      {
        href: "/docs/process/routines",
        label: "Routines",
        blurb: "The shipped catalogue, schedule shapes (cron / event / manual), standalone vs in-process routines.",
      },
      {
        href: "/docs/process/tracker-mapping-and-specialists",
        label: "Tracker mapping and specialists",
        blurb: "Which tickets are eligible, what role the routine plays — the two questions every routine answers at runtime.",
      },
      {
        href: "/docs/process/health",
        label: "Healthy and unhealthy routines",
        blurb: "Empty runs, hero agents, vanity throughput, drifted prompts, quiet failures — and the cures.",
      },
    ],
  },
  {
    label: "Operating",
    accent: "lilac",
    items: [
      {
        href: "/docs/operating/morning-loop",
        label: "The morning loop",
        blurb: "Workspace health → Inbox → shipped and in-progress → knowledge drift → audit. The order of moves.",
      },
      {
        href: "/docs/operating/audit-log",
        label: "The audit log",
        blurb: "What the log records, how to filter, who can see it, when to open it.",
      },
      {
        href: "/docs/operating/quiet-systems",
        label: "When the system quietly does nothing",
        blurb: "How to detect silent failures: read absence on a cadence, cross-check with the tracker, name the expectation.",
      },
    ],
  },
  {
    label: "Policies, secrets, evidence",
    accent: "sun",
    items: [
      {
        href: "/docs/policies/policies",
        label: "Policies",
        blurb: "Admin-authored standing rules injected into every agent's system prompt. Title, body, sort order, enabled.",
      },
      {
        href: "/docs/policies/secrets",
        label: "Secrets",
        blurb: "Workspace integration secrets, repo secrets, agent secrets, API tokens — four stores, four blast radii.",
      },
      {
        href: "/docs/policies/evidence",
        label: "The evidence checklist",
        blurb: "Tracker, PR, CI, comment, knowledge, audit — the five questions that test whether a trail is solid.",
      },
    ],
  },
  {
    label: "Local repo",
    accent: "coral",
    items: [
      {
        href: "/docs/developer/ship-folder",
        label: "The `.ship/` folder",
        blurb: "What's tracked vs ignored. Agent rule files outside the folder. What never belongs in `.ship/`.",
      },
      {
        href: "/docs/developer/shipctl",
        label: "shipctl — the local workbench",
        blurb: "Daily-use commands: doctor, verify, sync, config. The CLI is for engineers; the console is for operators.",
      },
      {
        href: "/docs/developer/authoring",
        label: "Authoring patterns and policies",
        blurb: "When to author. The small loop. Pattern vs knowledge vs policy. Where the deep schema reference lives.",
      },
      {
        href: "/docs/developer/bundle-updates",
        label: "Bundle updates",
        blurb: "What the 'Ship template update needed' banner means. Pins for slowing rollouts. When to skip an update.",
      },
    ],
  },
  {
    label: "Reference",
    accent: "aqua",
    items: [
      {
        href: "/docs/reference/cli",
        label: "shipctl command reference",
        blurb: "Every shipctl command, grouped by purpose, with one-line descriptions.",
      },
      {
        href: "/docs/reference/troubleshooting",
        label: "Troubleshooting",
        blurb: "Symptom → cause → fix for common console, GitHub App, tracker, knowledge, routine, and CLI issues.",
      },
      {
        href: "/docs/reference/glossary",
        label: "Glossary",
        blurb: "Every term in the manual, alphabetised, with a one-line definition and a chapter link.",
      },
    ],
  },
  {
    label: "Appendix",
    accent: "lilac",
    items: [
      {
        href: "/docs/appendix",
        label: "Friendly explainers",
        blurb: "Per-entry pages for non-technical readers — what GitHub is, where to get an OpenAI key, what a webhook is, etc. The wizard cross-links straight to the relevant entry.",
      },
    ],
  },
  {
    label: "Implementation spec",
    accent: "sun",
    items: [
      {
        href: "/docs/discovery",
        label: "Discovery contract",
        blurb: "Phase 0–4 interview an agent runs before opening its first PR. Normative.",
      },
      {
        href: "/docs/protocol",
        label: "Protocol (RFCs)",
        blurb: "Artifacts protocol, config schema, telemetry, adapters, folder layout.",
      },
      {
        href: "/docs/authoring",
        label: "Authoring (full reference)",
        blurb: "Schema-heavy contributor reference — folder layout, frontmatter, hashing. The friendly version is in Local repo.",
      },
    ],
  },
  {
    label: "Other",
    accent: "coral",
    items: [
      {
        href: "/docs/legal",
        label: "Legal",
        blurb: "License, copyright, versioning policy.",
      },
    ],
  },
];

export const ACCENT_TEXT: Record<DocsNavGroup["accent"], string> = {
  aqua: "text-aqua",
  lilac: "text-lilac",
  sun: "text-sun",
  coral: "text-coral",
};

/* Static class strings only — Tailwind JIT cannot extract dynamic values
 * built from template strings. Listing the full class names here keeps
 * the hover state working when the docs landing renders the cards. */
export const ACCENT_HOVER_BORDER: Record<DocsNavGroup["accent"], string> = {
  aqua: "hover:border-aqua/40",
  lilac: "hover:border-lilac/40",
  sun: "hover:border-sun/40",
  coral: "hover:border-coral/40",
};

/** Group label for a given href (used by the per-page hero kicker).
 *
 * Prefers an exact match; falls back to the longest prefix match so
 * sub-pages like /docs/protocol/rfc-0001-artifacts-protocol still inherit
 * the "Spec" group label from /docs/protocol. */
export function groupLabelForHref(href: string): string {
  let best: { label: string; len: number } | null = null;
  for (const g of DOCS_NAV) {
    for (const i of g.items) {
      if (i.href === href) return g.label;
      if (i.href !== "/docs" && (href === i.href || href.startsWith(i.href + "/"))) {
        if (!best || i.href.length > best.len) {
          best = { label: g.label, len: i.href.length };
        }
      }
    }
  }
  return best?.label ?? "Docs";
}
