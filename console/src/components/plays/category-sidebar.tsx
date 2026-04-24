import Link from "next/link";

import { cn } from "@/lib/cn";

/**
 * ``CategorySidebar`` — the real category browser for ``/plays``
 * (RFC-0010 / Wave 7 Phase 4 ticket P4-01).
 *
 * Replaces the placeholder 8-category list that used to live inline
 * in ``app/plays/page.tsx``. Each row shows a count derived from the
 * currently-visible catalog (after upstream filters), so the numbers
 * always match what the grid is actually about to render.
 *
 * The component is a server component because all of its inputs
 * (selected category / subcategory, counts, "critical only" toggle
 * URL state) are computed once on the server when the page is
 * rendered. URL navigation happens through standard ``next/link``
 * hrefs — no client state required.
 *
 * **Active highlight** mirrors the inset-aqua style the workspace
 * left nav uses (``app-shell.tsx``) so the visual language stays
 * consistent across the chrome.
 *
 * **Sub-facets** (Health checks → Security · Performance · …) are
 * only rendered when "Health checks" is the active category. Per
 * ticket spec we keep the DOM simple — no ``<details>``/``<summary>``
 * — they're just hidden by conditional render. Hover-to-reveal is
 * intentionally omitted because (a) it conflicts with mobile chip
 * row rendering, and (b) keyboard users wouldn't be able to reach
 * the sub-facets at all.
 */

export type PlayCategoryDef = {
  id: string;
  label: string;
  description: string;
  subcategories?: PlaySubcategoryDef[];
};

export type PlaySubcategoryDef = {
  id: string;
  label: string;
  description: string;
};

/**
 * Catalog-side category metadata (final mapping from
 * ``inbox-redesign-planning.md`` §2). Order matters — this is the
 * order the sidebar renders, and the ``All`` pseudo-row is prepended
 * at render time.
 */
export const PLAY_CATEGORIES: PlayCategoryDef[] = [
  {
    id: "code_review",
    label: "Code review",
    description: "PR-attached flows that run on every pull request.",
  },
  {
    id: "health_checks",
    label: "Health checks",
    description:
      "Scheduled scanners — security, performance, compliance, cost, ML quality.",
    subcategories: [
      {
        id: "security",
        label: "Security",
        description:
          "Dependency CVEs, license compatibility, PII / secrets leakage, IAM drift.",
      },
      {
        id: "performance",
        label: "Performance",
        description:
          "Bundle / binary size budgets, frame-time budgets, SLO health.",
      },
      {
        id: "compliance",
        label: "Compliance",
        description:
          "Consent flags, store metadata, localisation gaps, OS / HAL matrices.",
      },
      {
        id: "cost",
        label: "Cost",
        description:
          "Cloud cost deltas, terraform drift, env-var catalogue audits.",
      },
      {
        id: "ml_quality",
        label: "ML quality",
        description:
          "Data drift, bias / fairness, model eval, training reproducibility.",
      },
      {
        id: "other",
        label: "Other",
        description: "Accessibility, BOM / SBOM drift, miscellaneous scanners.",
      },
    ],
  },
  {
    id: "release_ops",
    label: "Release ops",
    description:
      "Release-train flows: notes, store submission, cert / compliance artifacts, OTA channels.",
  },
  {
    id: "incident_response",
    label: "Incident response",
    description:
      "Postmortems, on-call handoffs, runbook freshness, human-in-the-loop escalations.",
  },
  {
    id: "knowledge_docs",
    label: "Knowledge & Docs",
    description:
      "Doc / runbook freshness scanners, learning capture, knowledge seeding.",
  },
  {
    id: "planning_process",
    label: "Planning & Process",
    description:
      "Sprint planning, daily retros, BA / PM / Intake / Dev role personas.",
  },
  {
    id: "reviewers",
    label: "Reviewers",
    description:
      "Standalone role personas — tech / QA / security / mobile / desktop / ML reviewers.",
  },
];

const ALL_DESCRIPTION =
  "Every play in the catalog. Categorised plays appear under their primary category; multi-listed plays appear under each.";

const UNCATEGORIZED_DESCRIPTION =
  "Plays whose frontmatter doesn't carry a `category:` field yet. Bug Sibling B if anything ends up here permanently.";

export type CategoryCounts = {
  /** Total user-facing plays (i.e. those with a recognised category). */
  all: number;
  /** Plays per top-level category id. */
  byCategory: Record<string, number>;
  /** Plays per ``health_checks`` subcategory id. */
  bySubcategory: Record<string, number>;
  /** Plays whose ``category`` field is missing or unrecognised. */
  uncategorized: number;
};

export function CategorySidebar({
  selectedCategory,
  selectedSubcategory,
  criticalOnly,
  counts,
}: {
  /** Currently-selected category id, or ``"all"`` when no filter is set. */
  selectedCategory: string;
  selectedSubcategory: string | null;
  criticalOnly: boolean;
  counts: CategoryCounts;
}) {
  const showSubFacets = selectedCategory === "health_checks";

  return (
    <aside
      className="lg:w-[224px] lg:shrink-0"
      aria-label="Play categories"
    >
      <div className="mb-2 px-2.5 text-[10px] font-bold uppercase tracking-[0.18em] text-white/35">
        Categories
      </div>

      {/* Mobile: horizontal chip row. Desktop: vertical list. */}
      <ul className="flex flex-wrap gap-1.5 lg:flex-col lg:gap-0.5 lg:space-y-0.5">
        <CategoryRow
          href={buildHref({ category: "all", criticalOnly })}
          label="All"
          count={counts.all}
          active={selectedCategory === "all"}
          tooltip={ALL_DESCRIPTION}
        />
        <li
          aria-hidden
          className="hidden h-px w-full bg-white/[0.06] lg:my-1.5 lg:block"
        />
        {PLAY_CATEGORIES.map((cat) => {
          const isActive = selectedCategory === cat.id;
          const count = counts.byCategory[cat.id] ?? 0;
          return (
            <li key={cat.id} className="contents">
              <CategoryRow
                href={buildHref({ category: cat.id, criticalOnly })}
                label={cat.label}
                count={count}
                active={isActive}
                tooltip={cat.description}
              />
              {showSubFacets && cat.subcategories && cat.id === "health_checks"
                ? cat.subcategories.map((sub) => (
                    <SubcategoryRow
                      key={sub.id}
                      href={buildHref({
                        category: cat.id,
                        subcategory: sub.id,
                        criticalOnly,
                      })}
                      label={sub.label}
                      count={counts.bySubcategory[sub.id] ?? 0}
                      active={selectedSubcategory === sub.id}
                      tooltip={sub.description}
                    />
                  ))
                : null}
            </li>
          );
        })}
        {counts.uncategorized > 0 && (
          <>
            <li
              aria-hidden
              className="hidden h-px w-full bg-white/[0.06] lg:my-1.5 lg:block"
            />
            <CategoryRow
              href={buildHref({ category: "uncategorized", criticalOnly })}
              label="Uncategorized"
              count={counts.uncategorized}
              active={selectedCategory === "uncategorized"}
              tooltip={UNCATEGORIZED_DESCRIPTION}
            />
          </>
        )}
      </ul>
    </aside>
  );
}

function CategoryRow({
  href,
  label,
  count,
  active,
  tooltip,
}: {
  href: string;
  label: string;
  count: number;
  active: boolean;
  tooltip: string;
}) {
  return (
    <li>
      <Link
        href={href}
        title={tooltip}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group flex items-center gap-2 rounded-md px-2.5 py-1.5 text-xs transition",
          active
            ? "bg-white/[0.08] text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
            : "text-white/65 hover:bg-white/[0.04] hover:text-white",
        )}
      >
        <span className="flex-1 truncate">{label}</span>
        <span
          className={cn(
            "shrink-0 text-[10px] font-mono tabular-nums",
            active ? "text-white/70" : "text-white/35 group-hover:text-white/55",
          )}
        >
          {count}
        </span>
      </Link>
    </li>
  );
}

function SubcategoryRow({
  href,
  label,
  count,
  active,
  tooltip,
}: {
  href: string;
  label: string;
  count: number;
  active: boolean;
  tooltip: string;
}) {
  return (
    <li>
      <Link
        href={href}
        title={tooltip}
        aria-current={active ? "page" : undefined}
        className={cn(
          "group ml-3.5 flex items-center gap-2 rounded-md border-l border-white/10 pl-3 pr-2 py-1 text-[11px] transition",
          active
            ? "bg-aqua/[0.08] text-white shadow-[inset_2px_0_0_theme(colors.aqua)]"
            : "text-white/55 hover:bg-white/[0.04] hover:text-white/80",
        )}
      >
        <span className="flex-1 truncate">{label}</span>
        <span
          className={cn(
            "shrink-0 text-[10px] font-mono tabular-nums",
            active ? "text-white/65" : "text-white/30 group-hover:text-white/50",
          )}
        >
          {count}
        </span>
      </Link>
    </li>
  );
}

/**
 * Build a ``/plays`` URL for the given filter state. Excludes any
 * empty params so the canonical "All" URL stays the bare ``/plays``.
 *
 * Exported so the page (empty-state suggestions) and the grid
 * (critical-only chip toggle) can share the same builder.
 */
export function buildHref(opts: {
  category?: string;
  subcategory?: string;
  criticalOnly?: boolean;
  play?: string | null;
}): string {
  const params = new URLSearchParams();
  if (opts.category && opts.category !== "all") {
    params.set("category", opts.category);
  }
  if (opts.subcategory) {
    params.set("subcategory", opts.subcategory);
  }
  if (opts.criticalOnly) {
    params.set("critical", "true");
  }
  if (opts.play) {
    params.set("play", opts.play);
  }
  const qs = params.toString();
  return qs ? `/plays?${qs}` : "/plays";
}

/**
 * Reduce a list of catalog patterns + lane entries down to the
 * counts the sidebar needs. Pure function so the page can run it
 * once per request without thinking about state.
 *
 * - ``categoryOf`` maps a play to its top-level category id; pass
 *   the function rather than a precomputed list so callers can
 *   share categorisation logic with the grid filter.
 * - ``secondaryCategoriesOf`` lists extra categories the play
 *   should also count toward (RFC-0010 §2 multi-listed plays).
 * - ``subcategoryOf`` / ``criticalOf`` are extracted similarly so
 *   the sidebar component stays decoupled from the wire shape.
 */
export function countPlays<T>(
  plays: T[],
  selectors: {
    categoryOf: (p: T) => string | null;
    secondaryCategoriesOf?: (p: T) => string[];
    subcategoryOf: (p: T) => string | null;
  },
): CategoryCounts {
  const known = new Set(PLAY_CATEGORIES.map((c) => c.id));
  const out: CategoryCounts = {
    all: 0,
    byCategory: {},
    bySubcategory: {},
    uncategorized: 0,
  };
  for (const play of plays) {
    const primary = selectors.categoryOf(play);
    const seen = new Set<string>();
    if (primary && known.has(primary)) {
      out.all += 1;
      seen.add(primary);
      out.byCategory[primary] = (out.byCategory[primary] ?? 0) + 1;
      if (primary === "health_checks") {
        const sub = selectors.subcategoryOf(play);
        if (sub) {
          out.bySubcategory[sub] = (out.bySubcategory[sub] ?? 0) + 1;
        }
      }
    } else {
      out.uncategorized += 1;
    }
    const extras = selectors.secondaryCategoriesOf?.(play) ?? [];
    for (const extra of extras) {
      if (!known.has(extra) || seen.has(extra)) continue;
      seen.add(extra);
      out.byCategory[extra] = (out.byCategory[extra] ?? 0) + 1;
    }
  }
  return out;
}
