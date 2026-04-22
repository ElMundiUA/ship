import { notFound, redirect } from "next/navigation";
import type { ReactNode } from "react";

import { isApiConfigured } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Phase-1 two-mode shell: ``/r/[owner]/[repo]`` is the **repo mode**
 * segment. Everything nested under it renders the repo sidebar and
 * receives the resolved ``ApiActivatedRepo`` via its own fetch
 * (each server page re-resolves because React Server Components
 * don't share props with nested segments).
 *
 * The layout acts as an early gate: unauthenticated requests bounce
 * to ``/login?next=<path>`` and unknown ``owner/repo`` pairs 404 at
 * the shell rather than rendering an empty-state inside each page.
 *
 * When the backend is not configured (marketing-mock mode), the
 * layout falls through with no guarding so design-preview pages can
 * still exercise the shell.
 */

export default async function RepoLayout({
  params,
  children,
}: {
  params: Promise<RepoRouteParams>;
  children: ReactNode;
}) {
  const resolved = await params;
  const slug = slugFromParams(resolved);
  if (!slug) notFound();

  if (!isApiConfigured()) {
    return <>{children}</>;
  }

  const token = await getSessionToken();
  if (!token) {
    const next = encodeURIComponent(`/r/${slug}`);
    redirect(`/login?next=${next}`);
  }

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    const next = encodeURIComponent(`/r/${slug}`);
    redirect(`/login?next=${next}`);
  }
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();
  // "down" — let the nested page render its own down-state; the
  // shell shouldn't 500 because the backend is transiently offline.

  return <>{children}</>;
}
