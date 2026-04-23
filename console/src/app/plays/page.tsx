import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiCatalogPattern,
  type ApiLaneCatalogEntry,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listCatalogPatterns,
  listLaneCatalog,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { PlaysGrid } from "./plays-grid";

/**
 * ``/plays`` — unified catalog (RFC-0010 / P1-02).
 *
 * Merges the recurring side from ``listLaneCatalog`` (the recipes
 * that used to live on ``/lanes?tab=library``) with the one-shot
 * side from ``listCatalogPatterns({ mode: "request" })`` (the grid
 * that used to live on ``/requests``). Renders both flavours as
 * one ``PlayCard`` grid with a category sidebar.
 *
 * Categories are placeholders for this PR (every card lands in
 * "All"; clicking another category shows a "no patterns" state).
 * Real category mapping from frontmatter is tracked under P4-06.
 */

export const dynamic = "force-dynamic";

type SearchParamsBag = Record<string, string | string[] | undefined>;

const PLAY_CATEGORIES: { id: string; label: string }[] = [
  { id: "all", label: "All" },
  { id: "code_review", label: "Code review" },
  { id: "health_checks", label: "Health checks" },
  { id: "release_ops", label: "Release ops" },
  { id: "incident_response", label: "Incident response" },
  { id: "knowledge_docs", label: "Knowledge & Docs" },
  { id: "planning_process", label: "Planning & Process" },
  { id: "reviewers", label: "Reviewers" },
];

function pickCategory(params: SearchParamsBag): string {
  const raw = typeof params.category === "string" ? params.category : null;
  if (!raw) return "all";
  if (PLAY_CATEGORIES.some((c) => c.id === raw)) return raw;
  return "all";
}

export default async function PlaysPage({
  searchParams,
}: {
  searchParams?: Promise<SearchParamsBag>;
}) {
  const params = (await searchParams) ?? {};
  const category = pickCategory(params);

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
  try {
    [repos, lanes, requestPatterns] = await Promise.all([
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      listLaneCatalog(token).catch(() => [] as ApiLaneCatalogEntry[]),
      listCatalogPatterns({ mode: "request", token }).catch(
        () => [] as ApiCatalogPattern[],
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
        to schedule it on a cadence.
      </p>

      <div className="flex flex-col gap-6 lg:flex-row">
        <CategorySidebar selected={category} />

        <div className="min-w-0 flex-1">
          <PlaysGrid
            workspaceId={workspace.id}
            repos={sortedRepos}
            lanes={lanes}
            requestPatterns={requestPatterns}
            selectedCategory={category}
          />
        </div>
      </div>
    </AppShell>
  );
}

/**
 * Static category list. P4-01 will add active-state highlighting
 * and the actual frontmatter-driven filter; for now every category
 * but "All" intentionally returns an empty grid (acceptable
 * placeholder per ticket spec).
 */
function CategorySidebar({ selected: _selected }: { selected: string }) {
  return (
    <aside className="lg:w-[200px] lg:shrink-0">
      <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-white/35">
        Categories
      </div>
      <ul className="space-y-0.5 text-xs">
        {PLAY_CATEGORIES.map((cat) => (
          <li key={cat.id}>
            <Link
              href={
                cat.id === "all"
                  ? "/plays"
                  : `/plays?category=${encodeURIComponent(cat.id)}`
              }
              className="block rounded-md px-2.5 py-1.5 text-white/70 hover:bg-white/[0.04] hover:text-white"
            >
              {cat.label}
            </Link>
          </li>
        ))}
      </ul>
      <p className="mt-3 px-2.5 text-[10px] text-white/35">
        Real category mapping ships in P4-06. For now everything
        lives under &ldquo;All&rdquo;.
      </p>
    </aside>
  );
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
