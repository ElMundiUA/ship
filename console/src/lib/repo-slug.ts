/**
 * Phase-1 two-mode shell: the ``/r/[...slug]`` route family uses a
 * catch-all segment so the URL can mirror the GitHub ``owner/repo``
 * shape (``/r/acme/api/lanes``). Slash-agnostic backends (underscore,
 * double-dash) were considered and rejected — the readable form wins
 * because the chrome (header chip, breadcrumbs, share links) echoes
 * what the user typed in GitHub verbatim.
 *
 * All segment parsing + lookup funnels through here so the repo
 * layout, nav builder, and any future deep links agree on one
 * canonical shape.
 */

import type { ApiActivatedRepo } from "@/lib/api/client";

/**
 * Joins Next's ``[...slug]`` array into the ``owner/repo`` form, or
 * returns ``null`` when the shape is wrong. Extra trailing segments
 * (``/r/acme/api/lanes/active``) are dropped — only the first two
 * segments identify the repo.
 */
export function slugFromSegments(
  slug: string | string[] | undefined,
): string | null {
  if (!slug) return null;
  const parts = Array.isArray(slug) ? slug : [slug];
  const clean = parts.filter(Boolean);
  if (clean.length < 2) return null;
  return `${clean[0]}/${clean[1]}`;
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
