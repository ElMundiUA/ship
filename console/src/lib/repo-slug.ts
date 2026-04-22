/**
 * Phase-1 two-mode shell: the ``/r/[owner]/[repo]`` route family
 * uses two explicit dynamic segments so the URL can mirror the
 * GitHub ``owner/repo`` shape (``/r/acme/api/lanes``).
 * Slash-agnostic backends (underscore, double-dash) were considered
 * and rejected — the readable form wins because the chrome (header
 * chip, breadcrumbs, share links) echoes what the user typed in
 * GitHub verbatim.
 *
 * All segment parsing + lookup funnels through here so the repo
 * layout, nav builder, and any future deep links agree on one
 * canonical shape.
 *
 * A previous revision used a ``[...slug]`` catch-all. It silently
 * swallowed sub-paths (``/r/acme/api/lanes``) and rendered the repo
 * home for every nested URL, so every link in the repo sidebar and
 * on the home tiles looked broken. Explicit ``[owner]/[repo]``
 * segments lift that restriction: Next can now match concrete
 * child routes (``lanes``, ``requests``, …) underneath.
 */

import type { ApiActivatedRepo } from "@/lib/api/client";

export type RepoRouteParams = { owner?: string; repo?: string };

/**
 * Joins Next's ``[owner]/[repo]`` params into the ``owner/repo``
 * form, or returns ``null`` when either segment is missing/empty.
 */
export function slugFromParams(params: RepoRouteParams): string | null {
  const owner = params.owner?.trim();
  const repo = params.repo?.trim();
  if (!owner || !repo) return null;
  return `${owner}/${repo}`;
}

/**
 * Match a slug against the activated-repos list. Comparison is
 * case-insensitive on purpose — GitHub treats ``Acme/API`` the same
 * as ``acme/api`` for routing, and our slugs are copied from the
 * address bar where people inevitably paste mixed case.
 */
export function findRepoBySlug(
  repos: readonly ApiActivatedRepo[],
  slug: string,
): ApiActivatedRepo | null {
  const target = slug.toLowerCase();
  return repos.find((r) => r.full_name.toLowerCase() === target) ?? null;
}

/**
 * Build the canonical ``/r/<owner>/<repo>`` base path for a given
 * activated repo. Components that need to deep-link into a repo
 * surface go through this so there is exactly one place to update
 * when the segment shape changes.
 */
export function repoBasePath(repo: Pick<ApiActivatedRepo, "full_name">): string {
  return `/r/${repo.full_name}`;
}
