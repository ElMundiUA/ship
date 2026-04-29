/**
 * Docs sidebar nav, grouped by purpose. Used by docs/layout.tsx and the
 * /docs landing page card grid so labels and groupings can never drift.
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
    label: "Start here",
    accent: "aqua",
    items: [
      {
        href: "/getting-started",
        label: "Workspace setup",
        blurb:
          "Create a workspace, connect repos, bind the tracker, set policies, seed knowledge, and review the first Inbox items.",
      },
      {
        href: "/docs/concepts",
        label: "Product concepts",
        blurb:
          "Plain-language vocabulary for workspace, repo, tracker, Inbox, knowledge, evidence, and automation.",
      },
    ],
  },
  {
    label: "Workspace guides",
    accent: "lilac",
    items: [
      {
        href: "/docs/automations",
        label: "Automations",
        blurb: "Repeatable product and engineering actions that stay bounded, reviewable, and tied to evidence.",
      },
      {
        href: "/docs/concepts#inbox",
        label: "Inbox",
        blurb: "The attention surface for clarifications, improvements, approvals, failures, and exceptions.",
      },
      {
        href: "/docs/knowledge-buckets",
        label: "Knowledge",
        blurb: "How product facts, repo context, and standing rules reach agents and reviewers.",
      },
    ],
  },
  {
    label: "Technical reference",
    accent: "sun",
    items: [
      {
        href: "/docs/configuration",
        label: "Configuration",
        blurb: "The .ship/ layout and config fields for developers and platform teams.",
      },
      {
        href: "/docs/agent-matrix",
        label: "Agent matrix",
        blurb: "Supported agent ids, on-disk markers, install targets, adapter artifact for each.",
      },
      {
        href: "/docs/operating",
        label: "Operating",
        blurb: "Review blockers, decisions, shipped work, and evidence after setup.",
      },
      {
        href: "/docs/troubleshooting",
        label: "Troubleshooting",
        blurb: "Symptom → cause → fix for common workspace, console, automation, and evidence issues.",
      },
      {
        href: "/docs/authoring",
        label: "Authoring",
        blurb: "Write your own policy or procedure so repeated work stays reviewable.",
      },
    ],
  },
  {
    label: "Implementation spec",
    accent: "coral",
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
    ],
  },
  {
    label: "Other",
    accent: "lilac",
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
