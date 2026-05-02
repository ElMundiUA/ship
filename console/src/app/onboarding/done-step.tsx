/**
 * Post-onboarding "What just happened" step (P5-09).
 *
 * Replaces the static one-card success screen the wizard used to
 * render after the configure step. The new design surfaces:
 *
 *   - The seed PR per repo (open / merge link, branch, file count).
 *   - CODEOWNERS-derived routing rules + unresolved owners.
 *   - Synthetic lane count (so /inbox /automations /coverage start
 *     populated immediately rather than waiting for the merge).
 *   - Repo-intel harvest status (polled live until the row lands).
 *   - "What's next" CTA grid into the new IA.
 *
 * Server component because the per-repo data is workspace-scoped and
 * resolving the activated repos can be done in one cheap call. The
 * actual rendering of each repo card is delegated to
 * :component:`DoneResult` (a client component) so the cards can read
 * sessionStorage / poll intel / trigger retries without us shipping
 * the whole card tree as a client bundle.
 *
 * Multi-repo flow: the configure step navigates here with
 * ``?repo_id=<id>`` (single repo) or ``?repo_ids=a,b,c`` (multi). We
 * prefer that filter; if neither is set we render every activated
 * repo so a stale link still resolves to something useful.
 */

import Link from "next/link";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  type ApiActivatedRepo,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { DoneResult } from "./done-result";
import { WhatsNextGrid } from "./whats-next-grid";

export async function DoneStep({
  wsId,
  repoIdParam,
  repoIdsParam,
}: {
  wsId: string | null;
  /** ``?repo_id=...`` from the configure step (single-repo flow). */
  repoIdParam: string | null;
  /** ``?repo_ids=a,b,c`` from the configure step (multi-repo flow). */
  repoIdsParam: string | null;
}) {
  const apiConfigured = isApiConfigured();
  const sessionToken = await getSessionToken();

  let repos: ApiActivatedRepo[] = [];
  let loadError: string | null = null;
  if (apiConfigured && wsId) {
    try {
      const all = await listActivatedRepos(wsId, sessionToken ?? undefined);
      repos = filterRepos(all, repoIdParam, repoIdsParam);
    } catch (err) {
      if (err instanceof ApiUnavailableError) {
        loadError = "Backend not reachable.";
      } else if (err instanceof ApiHttpError) {
        loadError = `HTTP ${err.status} loading workspace repos.`;
      } else {
        loadError = "Couldn't load workspace repos.";
      }
    }
  }

  const repoCount = repos.length;
  const dashboardHref = wsId ? `/?ws=${encodeURIComponent(wsId)}` : "/";

  return (
    <section data-testid="onboarding-step-done">
      <p className="font-mono text-[11px] uppercase tracking-[0.3em] text-aqua/85">
        Done
      </p>
      <h1
        className="mt-2 font-display text-4xl font-bold leading-tight"
        data-testid="onboarding-done-title"
      >
        What just happened
      </h1>
      <p className="mt-3 max-w-2xl text-sm leading-relaxed text-white/70">
        {repoCount === 0
          ? "Bootstrap dispatched. Once at least one repo is wired, you'll see its result card here."
          : `Bootstrap dispatched for ${repoCount} ${
              repoCount === 1 ? "repo" : "repos"
            }. Each card below shows what we wired and where to go next.`}
      </p>

      {loadError && (
        <div className="mt-5 rounded-lg border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {loadError}
        </div>
      )}

      {repoCount === 0 && !loadError && (
        <NoReposEmptyState wsId={wsId} />
      )}

      {repoCount > 0 && (
        <div className="mt-7 space-y-4">
          {repos.map((repo) => (
            <DoneResult key={repo.id} workspaceId={wsId} repo={repo} />
          ))}
        </div>
      )}

      <WhatsNextGrid workspaceId={wsId} />

      <footer className="mt-10 flex flex-wrap items-center justify-between gap-3 border-t border-white/10 pt-5">
        <span className="text-[11px] text-white/45">
          All done — close this tab or jump back into the dashboard.
        </span>
        <Link
          href={dashboardHref}
          className="rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-2 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
        >
          Go to dashboard →
        </Link>
      </footer>
    </section>
  );
}

function NoReposEmptyState({ wsId }: { wsId: string | null }) {
  const reposHref = wsId
    ? `/onboarding?step=repos&ws=${encodeURIComponent(wsId)}`
    : "/onboarding?step=repos";
  return (
    <div className="mt-7 rounded-2xl border border-white/10 bg-white/[0.04] p-5 text-xs text-white/70 backdrop-blur-xl shadow-card">
      <p>
        No activated repos to show. Activate at least one and run the
        seed PR — the wizard will land you back here once it&apos;s
        done.
      </p>
      <Link
        href={reposHref}
        className="mt-3 inline-flex rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1.5 text-[11px] font-bold text-aqua hover:bg-aqua/[0.16]"
      >
        ← Back to repo picker
      </Link>
    </div>
  );
}

/**
 * Filter the workspace's activated repos against the navigation
 * intent the configure step expressed via URL params. Order is
 * preserved from the URL hint (so multi-repo flows render in the
 * configure-step order) and falls back to the API order when no hint
 * is set.
 */
function filterRepos(
  repos: ApiActivatedRepo[],
  repoIdParam: string | null,
  repoIdsParam: string | null,
): ApiActivatedRepo[] {
  const wanted = collectRepoIds(repoIdParam, repoIdsParam);
  if (wanted.length === 0) return repos;
  const byId = new Map(repos.map((r) => [r.id, r]));
  const ordered: ApiActivatedRepo[] = [];
  const seen = new Set<string>();
  for (const id of wanted) {
    const repo = byId.get(id);
    if (repo && !seen.has(repo.id)) {
      ordered.push(repo);
      seen.add(repo.id);
    }
  }
  // Fall back to the full list if the hinted ids were stale (e.g. user
  // copied a link after revoking a repo) — surfacing nothing would be
  // a worse UX than rendering everything we know about.
  return ordered.length > 0 ? ordered : repos;
}

function collectRepoIds(
  repoIdParam: string | null,
  repoIdsParam: string | null,
): string[] {
  const ids: string[] = [];
  if (repoIdParam) ids.push(repoIdParam);
  if (repoIdsParam) {
    for (const id of repoIdsParam.split(",")) {
      const trimmed = id.trim();
      if (trimmed) ids.push(trimmed);
    }
  }
  return ids;
}
