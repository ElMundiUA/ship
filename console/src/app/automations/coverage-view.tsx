import Link from "next/link";

import { CoverageFilters } from "@/components/coverage/coverage-filters";
import { CoverageRow } from "@/components/coverage/coverage-row";
import { Card, EmptyState } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiPlayCoverageOut,
  type ApiPlayCoverageRow,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listPlaysCoverage,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";

/**
 * Coverage tab body — RFC-0010 / P4-05.
 *
 * Renders the "which Plays are configured on which repos" surface
 * landed-on by ``/automations?tab=coverage`` (and the legacy
 * ``/fleet/adoption`` redirect). Layout:
 *
 *   [ summary strip ] N Plays · M repos · Z% overall · K critical gaps
 *   [ filter chips ] Category ▾ · Critical only · Has gaps · Clear
 *   [ rows ]         per-Play row + inline drill-down (covered/uncovered)
 *
 * Sibling subagent A owns ``GET /v1/workspaces/{ws}/plays/coverage``
 * — the data source. When that endpoint is unavailable (backend
 * down, 401, fetch error) we fall back to a static ``MockView`` so
 * the marketing-style preview deployment still has something to
 * show. This mirrors the same convention ``/inbox`` and
 * ``/integrations`` use.
 *
 * Decision (per planning doc): "overall coverage %" is the simple
 * unweighted average of ``coverage_pct`` across rows. Picked simple
 * over weighted (described in the brief) because v1 only needs a
 * directional signal — the row-level bars carry the per-play truth.
 */

type CoverageFiltersState = {
  category: string | null;
  criticalOnly: boolean;
  hasGaps: boolean;
};

export async function CoverageView({
  workspaceId,
  repos,
  basePath,
  filters,
  token,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  basePath: string;
  filters: CoverageFiltersState;
  token: string;
}) {
  if (!isApiConfigured()) {
    return <MockView basePath={basePath} reason="Backend not configured" />;
  }

  let data: ApiPlayCoverageOut;
  try {
    data = await listPlaysCoverage(workspaceId, {
      category: filters.category,
      criticalOnly: filters.criticalOnly,
      hasGaps: filters.hasGaps,
      token,
    });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      // Sibling A's endpoint hasn't shipped yet — fall back to mock
      // data so the tab is still renderable end-to-end.
      return <MockView basePath={basePath} reason="Coverage endpoint not yet deployed" />;
    }
    if (err instanceof ApiUnavailableError) {
      return <MockView basePath={basePath} reason="Backend unreachable" />;
    }
    return <MockView basePath={basePath} reason="Couldn't load coverage data" />;
  }

  const repoSlugById = buildRepoSlugLookup(repos);

  return (
    <CoverageBody
      data={data}
      repoSlugById={repoSlugById}
      basePath={basePath}
      filters={filters}
      activatedRepoCount={repos.length}
    />
  );
}

function CoverageBody({
  data,
  repoSlugById,
  basePath,
  filters,
  activatedRepoCount,
}: {
  data: ApiPlayCoverageOut;
  repoSlugById: (id: string) => string | null;
  basePath: string;
  filters: CoverageFiltersState;
  activatedRepoCount: number;
}) {
  const stats = computeSummary(data);

  // Empty state: workspace has zero activated repos. Plays don't
  // mean anything without a repo to apply them to, so push the user
  // toward repo activation instead of a blank list.
  if (activatedRepoCount === 0 && data.activated_repos_total === 0) {
    return (
      <>
        <CoverageFilters basePath={basePath} state={filters} />
        <EmptyState
          title="No activated repos yet"
          body="Add a repo first to see Plays coverage across your fleet."
          action={
            <Link
              href="/r"
              className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
            >
              Add a repo →
            </Link>
          }
        />
      </>
    );
  }

  return (
    <>
      <SummaryStrip stats={stats} />
      <CoverageFilters basePath={basePath} state={filters} />

      {data.rows.length === 0 ? (
        <EmptyFilteredState basePath={basePath} filters={filters} />
      ) : (
        <div className="flex flex-col gap-2">
          {data.rows.map((row) => (
            <CoverageRow
              key={row.play_key}
              row={row}
              repoSlugById={repoSlugById}
            />
          ))}
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------
// Summary strip
// ---------------------------------------------------------------------

type SummaryStats = {
  playCount: number;
  repoCount: number;
  overallPct: number;
  criticalGapCount: number;
};

function computeSummary(data: ApiPlayCoverageOut): SummaryStats {
  const playCount = data.rows.length;
  const repoCount = data.activated_repos_total;

  // Simple unweighted average of coverage_pct across rows.
  // (See file-level decision note for why we picked this over the
  // weighted variant the brief described.)
  const sum = data.rows.reduce((acc, r) => acc + (r.coverage_pct ?? 0), 0);
  const overallPct =
    playCount === 0 ? 0 : Math.round((sum / playCount) * 100);

  const criticalGapCount = data.rows.filter(
    (r) => r.critical && r.coverage_pct < 1,
  ).length;

  return { playCount, repoCount, overallPct, criticalGapCount };
}

function SummaryStrip({ stats }: { stats: SummaryStats }) {
  return (
    <Card className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3" padded={false}>
      <SummaryStat label="Plays" value={stats.playCount.toString()} />
      <Divider />
      <SummaryStat label="Repos" value={stats.repoCount.toString()} />
      <Divider />
      <SummaryStat label="Overall coverage" value={`${stats.overallPct}%`} />
      <Divider />
      <div className="flex items-center gap-2">
        {stats.criticalGapCount > 0 && (
          <span
            aria-hidden
            className="h-2 w-2 rounded-full bg-coral shadow-[0_0_0_3px_rgba(244,63,94,0.18)]"
          />
        )}
        <SummaryStat
          label="Critical gaps"
          value={stats.criticalGapCount.toString()}
          tone={stats.criticalGapCount > 0 ? "coral" : "neutral"}
        />
      </div>
    </Card>
  );
}

function SummaryStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: string;
  tone?: "neutral" | "coral";
}) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span
        className={cn(
          "font-display text-base font-bold tabular-nums",
          tone === "coral" ? "text-coral" : "text-white",
        )}
      >
        {value}
      </span>
      <span className="text-[11px] uppercase tracking-wider text-white/55">
        {label}
      </span>
    </div>
  );
}

function Divider() {
  return <span aria-hidden className="h-4 w-px bg-white/10" />;
}

// ---------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------

function EmptyFilteredState({
  basePath,
  filters,
}: {
  basePath: string;
  filters: CoverageFiltersState;
}) {
  const usedHasGaps = filters.hasGaps;
  return (
    <EmptyState
      title={
        usedHasGaps
          ? "Nothing uncovered."
          : "No plays match this filter."
      }
      body={
        usedHasGaps
          ? "Every Play in this slice is at 100%. Try the All view to see the full catalog."
          : "Try clearing the active filters."
      }
      action={
        <Link
          href={`${basePath}?tab=coverage`}
          className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
        >
          Clear filters
        </Link>
      }
    />
  );
}

// ---------------------------------------------------------------------
// Repo slug lookup
// ---------------------------------------------------------------------

function buildRepoSlugLookup(
  repos: ApiActivatedRepo[],
): (id: string) => string | null {
  const map = new Map<string, string>();
  for (const r of repos) map.set(r.id, r.full_name);
  return (id: string) => map.get(id) ?? null;
}

// ---------------------------------------------------------------------
// MockView — shown when the BE coverage endpoint isn't reachable.
// Mirrors /inbox + /integrations conventions.
// ---------------------------------------------------------------------

const MOCK_REPOS: { id: string; full_name: string }[] = [
  { id: "mock-repo-1", full_name: "acme/widgets" },
  { id: "mock-repo-2", full_name: "acme/portal" },
  { id: "mock-repo-3", full_name: "acme/legacy" },
  { id: "mock-repo-4", full_name: "acme/sandbox" },
];

const MOCK_ROWS: ApiPlayCoverageRow[] = [
  {
    play_key: "flow-pr-self-review",
    play_name: "PR self-review",
    category: "code_review",
    critical: true,
    activated_repos_total: 4,
    assignments_count: 1,
    coverage_pct: 0.25,
    repos_covered: ["mock-repo-1"],
    repos_uncovered: ["mock-repo-2", "mock-repo-3", "mock-repo-4"],
  },
  {
    play_key: "scan-security-deps",
    play_name: "Security dependency scan",
    category: "health_checks",
    critical: true,
    activated_repos_total: 4,
    assignments_count: 0,
    coverage_pct: 0,
    repos_covered: [],
    repos_uncovered: [
      "mock-repo-1",
      "mock-repo-2",
      "mock-repo-3",
      "mock-repo-4",
    ],
  },
  {
    play_key: "scan-test-coverage",
    play_name: "Test coverage delta",
    category: "code_review",
    critical: false,
    activated_repos_total: 4,
    assignments_count: 2,
    coverage_pct: 0.5,
    repos_covered: ["mock-repo-1", "mock-repo-2"],
    repos_uncovered: ["mock-repo-3", "mock-repo-4"],
  },
  {
    play_key: "flow-release-notes",
    play_name: "Release notes",
    category: "release_ops",
    critical: true,
    activated_repos_total: 4,
    assignments_count: 4,
    coverage_pct: 1,
    repos_covered: [
      "mock-repo-1",
      "mock-repo-2",
      "mock-repo-3",
      "mock-repo-4",
    ],
    repos_uncovered: [],
  },
];

function MockView({ basePath, reason }: { basePath: string; reason: string }) {
  const data: ApiPlayCoverageOut = {
    activated_repos_total: 4,
    rows: MOCK_ROWS,
  };
  const slug = (id: string) =>
    MOCK_REPOS.find((r) => r.id === id)?.full_name ?? null;

  return (
    <>
      <Card className="mb-4 flex items-center gap-2 border-sun/30 bg-sun/5 px-3 py-2 text-xs text-sun/95" padded={false}>
        <span className="rounded-full bg-sun/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-sun">
          mock
        </span>
        <span>Coverage preview · {reason}. Sample data shown.</span>
      </Card>

      <CoverageBody
        data={data}
        repoSlugById={slug}
        basePath={basePath}
        filters={{ category: null, criticalOnly: false, hasGaps: false }}
        activatedRepoCount={MOCK_REPOS.length}
      />
    </>
  );
}
