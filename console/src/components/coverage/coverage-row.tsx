import Link from "next/link";

import { Badge } from "@/components/ui";
import type { ApiPlayCoverageRow } from "@/lib/api/client";

import { CoverageProgressBar } from "./coverage-progress-bar";

/**
 * One Play row in the Coverage list.
 *
 * Layout (left → right):
 *
 *   [critical badge?] [Play name]                [category chip]
 *   [progress bar with X/Y · Z% overlay]              [Drill ▸]
 *   ▼ inline drill-down panel (covered / uncovered split)
 *
 * Uses native ``<details>`` for the drill-down so the row is SSR-
 * friendly (no client JS for expand/collapse, deep-links can hash
 * to ``#coverage-{play_key}`` and have it open by default via
 * ``open``). The "Add to .ship" / per-repo links resolve UUIDs
 * against the activated-repos lookup the page level passes in.
 */

type RepoSlugLookup = (repoId: string) => string | null;

const CATEGORY_LABELS: Record<string, string> = {
  code_review: "Code review",
  health_checks: "Health checks",
  release_ops: "Release ops",
  incident_response: "Incident response",
  knowledge_docs: "Knowledge & Docs",
  planning_process: "Planning & Process",
  reviewers: "Reviewers",
  uncategorized: "Uncategorized",
};

function categoryLabel(category: string): string {
  if (CATEGORY_LABELS[category]) return CATEGORY_LABELS[category];
  // Unknown categories: show the key verbatim, snake → Title Case.
  return category
    .split("_")
    .filter(Boolean)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ");
}

export function CoverageRow({
  row,
  repoSlugById,
}: {
  row: ApiPlayCoverageRow;
  repoSlugById: RepoSlugLookup;
}) {
  const showCriticalBadge = row.critical && row.coverage_pct < 1;
  const coveredCount = row.repos_covered.length;
  const uncoveredCount = row.repos_uncovered.length;

  return (
    <details
      id={`coverage-${row.play_key}`}
      className="group rounded-2xl border border-white/10 bg-white/[0.04] backdrop-blur-xl shadow-card transition open:border-white/20"
    >
      <summary className="flex cursor-pointer list-none flex-col gap-3 p-4 hover:bg-white/[0.02]">
        <div className="flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            {showCriticalBadge && (
              <Badge tone="err" dot>
                Critical
              </Badge>
            )}
            <span className="truncate text-sm font-semibold text-white">
              {row.play_name || row.play_key}
            </span>
            <code className="hidden truncate font-mono text-[10px] text-white/35 md:inline">
              {row.play_key}
            </code>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Badge tone="neutral">{categoryLabel(row.category)}</Badge>
            <span
              aria-hidden
              className="inline-flex items-center gap-1 rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-1 text-[11px] font-semibold text-white/75 transition group-open:border-aqua/40 group-open:text-aqua"
            >
              <span>Drill</span>
              <span className="transition-transform group-open:rotate-90">
                ▸
              </span>
            </span>
          </div>
        </div>
        <CoverageProgressBar
          pct={row.coverage_pct}
          critical={row.critical}
          covered={row.assignments_count}
          total={row.activated_repos_total}
        />
      </summary>

      <div className="border-t border-white/10 px-4 pb-4 pt-3">
        <div className="grid gap-4 md:grid-cols-2">
          <RepoColumn
            title="Covered"
            count={coveredCount}
            repos={row.repos_covered}
            repoSlugById={repoSlugById}
            kind="covered"
          />
          <RepoColumn
            title="Uncovered"
            count={uncoveredCount}
            repos={row.repos_uncovered}
            repoSlugById={repoSlugById}
            kind="uncovered"
          />
        </div>

        {uncoveredCount > 0 && (
          <div className="mt-4 flex items-center justify-end">
            {/* TODO(P4-?): bulk apply route — wires every uncovered repo to this
                play in a single PR. Placeholder 404 link for now so the CTA is
                discoverable while the wizard is being designed. */}
            <Link
              href={`/automations/coverage/${encodeURIComponent(
                row.play_key,
              )}/apply-all`}
              className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/10 px-3 py-1.5 text-xs font-semibold text-aqua transition hover:border-aqua/70 hover:bg-aqua/20"
            >
              ↗ Apply to all uncovered ({uncoveredCount})
            </Link>
          </div>
        )}
      </div>
    </details>
  );
}

function RepoColumn({
  title,
  count,
  repos,
  repoSlugById,
  kind,
}: {
  title: string;
  count: number;
  repos: string[];
  repoSlugById: RepoSlugLookup;
  kind: "covered" | "uncovered";
}) {
  return (
    <section className="rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-[11px] font-bold uppercase tracking-[0.18em] text-white/55">
          {title}
        </h4>
        <span className="text-[11px] font-semibold tabular-nums text-white/55">
          {count}
        </span>
      </div>
      {count === 0 ? (
        <p className="text-xs text-white/40">
          {kind === "covered"
            ? "No repos have this play wired yet."
            : "Every activated repo is covered."}
        </p>
      ) : (
        <ul className="divide-y divide-white/5">
          {repos.map((repoId) => {
            const slug = repoSlugById(repoId);
            return (
              <li
                key={repoId}
                className="flex items-center justify-between gap-2 py-1.5 text-xs"
              >
                <span className="min-w-0 truncate text-white/80">
                  {slug ?? (
                    <code className="font-mono text-[10px] text-white/35">
                      {repoId.slice(0, 8)}…
                    </code>
                  )}
                </span>
                {slug && <RepoAction kind={kind} slug={slug} />}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function RepoAction({
  kind,
  slug,
}: {
  kind: "covered" | "uncovered";
  slug: string;
}) {
  if (kind === "covered") {
    return (
      <Link
        href={`/r/${slug}`}
        className="shrink-0 text-[11px] font-semibold text-aqua/80 hover:text-aqua"
      >
        → View .ship
      </Link>
    );
  }
  // TODO(P4-?): per-repo "Add to .ship" wizard. For now, a plain
  // link to the existing repo settings page where the user can wire
  // the play manually. The one-click variant ships in a follow-up.
  return (
    <Link
      href={`/r/${slug}/settings`}
      className="shrink-0 text-[11px] font-semibold text-coral/80 hover:text-coral"
    >
      + Add to .ship
    </Link>
  );
}
