import { redirect } from "next/navigation";

import type { RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode knowledge detail
 * (``/r/<owner>/<repo>/knowledge/<slug>``).
 *
 * Knowledge buckets live at the workspace tier (they can be scoped
 * to a repo, but the detail surface — articles, connector status,
 * Distiller runs, CLI hints — is the same whichever route the user
 * arrived through). Rather than ship a 600-line clone that would
 * drift from the workspace page, the repo-mode route redirects to
 * ``/knowledge/<slug>`` and lets that surface do the work.
 *
 * The repo-mode grid on the parent page still links directly to
 * ``/knowledge/<slug>``; this route exists so hand-pasted repo-mode
 * URLs (``/r/acme/api/knowledge/runbooks``) resolve cleanly instead
 * of 404-ing.
 */
export default async function RepoKnowledgeDetailRedirect({
  params,
}: {
  params: Promise<RepoRouteParams & { id: string }>;
}) {
  const { id } = await params;
  redirect(`/knowledge/${encodeURIComponent(id)}`);
}
