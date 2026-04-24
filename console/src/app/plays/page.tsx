import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  CategorySidebar,
  PLAY_CATEGORIES,
  buildHref,
  countPlays,
} from "@/components/plays/category-sidebar";
import { PlayDetailDrawer } from "@/components/plays/play-detail-drawer";
import { PlayDetailDrawerShell } from "@/components/plays/play-detail-drawer-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiCatalogPattern,
  type ApiLaneCatalogEntry,
  type LatestRunForPlay,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listCatalogPatterns,
  listLaneCatalog,
  listLatestRunsByPlay,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { PlaysGrid, type UnifiedPlay } from "./plays-grid";

/**
 * ``/plays`` — unified catalog (RFC-0010 / Wave 7 Phase 4).
 *
 * Server component. Owns:
 *
 *   1. Auth + workspace bootstrap.
 *   2. Three parallel fetches (``Promise.all``) — activated repos,
 *      lane catalog, request-mode catalog patterns, latest-run-per-
 *      play map. The latest-run map drives the per-card "Last run"
 *      mini-strip (P4-03); we batch the workspace's pipelines into
 *      one fan-out so the strip doesn't N+1 the backend.
 *   3. URL state parsing (``?category=`` · ``?subcategory=`` ·
 *      ``?critical=`` · ``?play=``) and the corresponding filter
 *      pass against the merged play list.
 *   4. Sidebar + grid + (optional) drawer rendering.
 *
 * Categories per ``inbox-redesign-planning.md`` §2 — see
 * :func:`categoryOf` for the wire-shape projection.
 *
 * Drawer mechanics (P4-02): we picked ``?play=<id>`` over a
 * URL hash because (a) the page is a server component and reads
 * search params at request time — hashes never reach the server, and
 * (b) sharing a deep-link to a play needs server-side resolution
 * (404 if the id doesn't exist). The hash approach forces all the
 * resolution into client JS which defeats the point of the
 * ``view-source-friendly`` server render.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

function pickString(
  bag: SearchParamsBag,
  key: string,
): string | null {
  const raw = bag[key];
  if (typeof raw === "string" && raw.length > 0) return raw;
  if (Array.isArray(raw) && raw.length > 0 && typeof raw[0] === "string") {
    return raw[0];
  }
  return null;
}

function isKnownCategory(id: string): boolean {
  if (id === "all" || id === "uncategorized") return true;
  return PLAY_CATEGORIES.some((c) => c.id === id);
}

function isKnownSubcategory(category: string, sub: string): boolean {
  const cat = PLAY_CATEGORIES.find((c) => c.id === category);
  if (!cat || !cat.subcategories) return false;
  return cat.subcategories.some((s) => s.id === sub);
}

export default async function PlaysPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  const params = (await searchParams) ?? {};
  const rawCategory = pickString(params, "category") ?? "all";
  const selectedCategory = isKnownCategory(rawCategory) ? rawCategory : "all";
  const rawSub = pickString(params, "subcategory");
  const selectedSubcategory =
    rawSub && selectedCategory && isKnownSubcategory(selectedCategory, rawSub)
      ? rawSub
      : null;
  const criticalOnly = pickString(params, "critical") === "true";
  const playParam = pickString(params, "play");

  if (!isApiConfigured()) {
    return (
      <AppShell title="Plays" kicker="CATALOG">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load the unified play catalog."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fplays");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fplays");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let repos: ApiActivatedRepo[] = [];
  let lanes: ApiLaneCatalogEntry[] = [];
  let requestPatterns: ApiCatalogPattern[] = [];
  let lastRunByPlay = new Map<string, LatestRunForPlay>();
  try {
    [repos, lanes, requestPatterns, lastRunByPlay] = await Promise.all([
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      listLaneCatalog(token).catch(() => [] as ApiLaneCatalogEntry[]),
      listCatalogPatterns({ mode: "request", token }).catch(
        () => [] as ApiCatalogPattern[],
      ),
      // P4-03 — batched per-workspace fan-out. See
      // ``listLatestRunsByPlay`` for the N+1 mitigation rationale and
      // the BE follow-up TODO.
      listLatestRunsByPlay(workspace.id, token).catch(
        () => new Map<string, LatestRunForPlay>(),
      ),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fplays");
    }
    return renderUnavailable(err);
  }

  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );

  const allPlays = mergePlays(lanes, requestPatterns);
  const candidates = criticalOnly
    ? allPlays.filter((p) => p.kind === "request" && p.pattern.critical === true)
    : allPlays;
  const counts = countPlays(candidates, {
    categoryOf,
    secondaryCategoriesOf,
    subcategoryOf,
  });
  const visiblePlays = candidates.filter((play) =>
    matchesCategoryFilter(play, selectedCategory, selectedSubcategory),
  );

  // Resolve the deep-linked play (P4-02) against the FULL catalog so
  // operators can land on a play even if the current category /
  // critical-only filter would have hidden it. Render the drawer
  // alongside the grid; the grid stays mounted (visually muted by
  // the backdrop) so closing the drawer feels instant.
  const playForDrawer = playParam
    ? findPlayById(allPlays, playParam)
    : null;
  const closeHref = buildHref({
    category: selectedCategory,
    subcategory: selectedSubcategory ?? undefined,
    criticalOnly,
  });

  return (
    <AppShell
      title="Plays"
      kicker="CATALOG"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      scope={{
        repos: sortedRepos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: sortedRepos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/automations"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Automations →
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Every Play Ship knows about — recurring lanes and one-shot
        agent runs in one grid. Hit{" "}
        <span className="font-semibold text-white/80">Run now</span> to
        dispatch a one-shot against the active repo, or{" "}
        <span className="font-semibold text-white/80">Automate</span>{" "}
        to schedule it on a cadence. Click any card to open its
        details.
      </p>

      <div className="flex flex-col gap-6 lg:flex-row">
        <CategorySidebar
          selectedCategory={selectedCategory}
          selectedSubcategory={selectedSubcategory}
          criticalOnly={criticalOnly}
          counts={counts}
        />

        <div className="min-w-0 flex-1">
          <PlaysGrid
            workspaceId={workspace.id}
            repos={sortedRepos}
            visiblePlays={visiblePlays}
            lastRunByPlay={lastRunByPlay}
            selectedCategory={selectedCategory}
            selectedSubcategory={selectedSubcategory}
            criticalOnly={criticalOnly}
          />
        </div>
      </div>

      {playForDrawer && (
        <PlayDetailDrawerShell closeHref={closeHref}>
          <PlayDetailDrawer
            play={
              playForDrawer.kind === "request"
                ? {
                    kind: "request",
                    id: playForDrawer.id,
                    pattern: playForDrawer.pattern,
                  }
                : {
                    kind: "lane",
                    id: playForDrawer.id,
                    entry: playForDrawer.entry,
                  }
            }
          />
        </PlayDetailDrawerShell>
      )}
    </AppShell>
  );
}

// -- shape helpers (server-only) ---------------------------------------------

function mergePlays(
  lanes: ApiLaneCatalogEntry[],
  requestPatterns: ApiCatalogPattern[],
): UnifiedPlay[] {
  // Index request patterns by id so we can dedupe lane recipes that
  // also expose a request mode (avoids the same Play showing twice).
  // The request-flavoured row wins because it carries dispatch
  // metadata + inputs.
  const requestById = new Map<string, ApiCatalogPattern>();
  for (const p of requestPatterns) {
    requestById.set(p.id, p);
  }

  const out: UnifiedPlay[] = [];
  for (const p of requestPatterns) {
    out.push({
      kind: "request",
      id: p.id,
      title: p.name ?? p.id,
      description: p.description || p.id,
      tags: [p.category, ...p.tags.slice(0, 2)].filter(
        (v): v is string => !!v,
      ),
      pattern: p,
    });
  }
  for (const entry of lanes) {
    const patternId = entry.pattern ?? entry.kind;
    if (requestById.has(patternId)) continue;
    out.push({
      kind: "lane",
      id: patternId,
      title: entry.title,
      description: entry.summary,
      tags: [
        entry.event ? `event:${entry.event}` : null,
        entry.schedule ? "scheduled" : null,
      ].filter((v): v is string => !!v),
      entry,
    });
  }
  out.sort((a, b) => a.title.localeCompare(b.title));
  return out;
}

function findPlayById(plays: UnifiedPlay[], id: string): UnifiedPlay | null {
  return plays.find((p) => p.id === id) ?? null;
}

function categoryOf(play: UnifiedPlay): string | null {
  if (play.kind === "request") {
    return play.pattern.category ?? null;
  }
  // Lane catalog entries don't carry a ``category`` field on the wire
  // yet — sibling B's PR adds it. Until then we pass them through as
  // uncategorised (the sidebar's "Uncategorized" footer link surfaces
  // the count when N > 0).
  return null;
}

function secondaryCategoriesOf(play: UnifiedPlay): string[] {
  if (play.kind === "request") {
    return play.pattern.secondary_categories ?? [];
  }
  return [];
}

function subcategoryOf(play: UnifiedPlay): string | null {
  if (play.kind === "request") {
    return play.pattern.subcategory ?? null;
  }
  return null;
}

function matchesCategoryFilter(
  play: UnifiedPlay,
  category: string,
  subcategory: string | null,
): boolean {
  const primary = categoryOf(play);
  const secondaries = secondaryCategoriesOf(play);
  const sub = subcategoryOf(play);

  if (category === "all") {
    // "All" excludes uncategorised plays per the gotcha note in the
    // ticket — the goal is "everything categorised". Items still
    // surface via the explicit Uncategorized footer link.
    return !!primary;
  }
  if (category === "uncategorized") {
    return !primary;
  }
  const inPrimary = primary === category;
  const inSecondary = secondaries.includes(category);
  if (!inPrimary && !inSecondary) return false;
  if (subcategory && category === "health_checks") {
    if (sub !== subcategory) return false;
  }
  return true;
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Plays" kicker="CATALOG">
      <Card>
        <CardHeader
          title="Couldn't load the play catalog"
          subtitle={
            isUnavailable
              ? "Backend is unreachable. Try again in a few seconds."
              : "Something went wrong."
          }
        />
      </Card>
    </AppShell>
  );
}
