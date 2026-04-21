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
  /* The Get started slot in the top nav points at /docs (this section).
   * The setup wizard itself lives at the top-level /getting-started URL
   * but appears here as the first sidebar entry so people who clicked
   * "Get started" can find it in one more click. */
  {
    label: "Start here",
    accent: "aqua",
    items: [
      {
        href: "/getting-started",
        label: "Setup wizard",
        blurb:
          "Pick an adoption path, generate the exact shipctl init command, and prompt your agent — three paths, one command.",
      },
    ],
  },
  {
    label: "Reference",
    accent: "lilac",
    items: [
      {
        href: "/docs/concepts",
        label: "Concepts",
        blurb: "Vocabulary: artifact, kind, channel, pin, install_target, adapter, preset, marker.",
      },
      {
        href: "/docs/configuration",
        label: "Configuration",
        blurb: "Every field of .ship/config.yml plus the on-disk layout under .ship/.",
      },
      {
        href: "/docs/lanes",
        label: "Lanes",
        blurb: "The lanes: block, shipctl run + lanes install, and the Console /lanes page.",
      },
      {
        href: "/docs/agent-matrix",
        label: "Agent matrix",
        blurb: "Supported agent ids, on-disk markers, install targets, adapter artifact for each.",
      },
    ],
  },
  {
    label: "Guides",
    accent: "sun",
    items: [
      {
        href: "/docs/operating",
        label: "Operating",
        blurb: "Day-2 work: pin a version, switch channel, read verify, debug sync, draft feedback.",
      },
      {
        href: "/docs/troubleshooting",
        label: "Troubleshooting",
        blurb: "Symptom → cause → fix for the errors you actually hit when running shipctl.",
      },
      {
        href: "/docs/authoring",
        label: "Authoring",
        blurb: "Write your own pattern, tool, workflow, collection, preset, or adapter.",
      },
    ],
  },
  {
    label: "Spec",
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
