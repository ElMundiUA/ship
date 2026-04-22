import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { findRepoBySlug, slugFromSegments } from "@/lib/repo-slug";

/**
 * Phase-1 two-mode shell: ``/r/[...slug]`` is the **repo mode**
 * segment. Everything nested under it renders the repo sidebar and
 * receives the resolved ``ApiActivatedRepo`` via the shared context.
 *
 * The layout is the single place that maps ``owner/repo`` → ``repoId``,
 * so individual pages don't each do their own resolution dance.
 * Unauthenticated requests bounce to ``/login?next=<path>``; unknown
 * repos produce a 404 (so shared links to decommissioned repos stop
 * at the wall instead of rendering a dashboard against a random
 * fallback repo).
 *
 * When the backend is not configured (marketing-mock mode), the
 * layout falls through with no guarding so design-preview pages can
 * still exercise the shell.
 */

type Params = { slug?: string[] };

export default async function RepoLayout({
  params,
  children,
}: {
  params: Promise<Params>;
  children: ReactNode;
}) {
  const resolved = await params;
  const slug = slugFromSegments(resolved.slug);
  if (!slug) notFound();

  if (!isApiConfigured()) {
    return <>{children}</>;
  }

  const token = await getSessionToken();
  if (!token) {
    const next = encodeURIComponent(`/r/${slug}`);
    redirect(`/login?next=${next}`);
  }

  try {
    const workspaces = await listWorkspaces(token);
    if (workspaces.length === 0) redirect("/onboarding?step=github");
    const workspace = workspaces[0];
    const repos = await listActivatedRepos(workspace.id, token);
    const repo = findRepoBySlug(repos, slug);
    if (!repo) notFound();
  } catch (err) {
    // 401/403 → bounce to login; other failures let the page render
    // and surface their own error state rather than 500 the shell.
    if (err instanceof ApiHttpError && err.status === 401) {
      const next = encodeURIComponent(`/r/${slug}`);
      redirect(`/login?next=${next}`);
    }
    if (err instanceof ApiUnavailableError) {
      // Fall through; the page's own fetch will render the down state.
    }
  }

  return <>{children}</>;
}
