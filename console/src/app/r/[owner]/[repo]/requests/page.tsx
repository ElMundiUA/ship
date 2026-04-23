/**
 * MIGRATED: /r/[owner]/[repo]/requests → /runs?scope=repo&repo=<id> per RFC-0010 P1-07.
 *
 * Per-repo requests folded into the workspace ``/runs`` surface
 * with a ``scope=repo`` chip. ``RequestsCatalog`` (the actual
 * dispatch UI) is owned by sibling subagent B's ``/plays`` page
 * now; this redirect lands users on the run history filtered to
 * the originating repo.
 */
import { redirect, RedirectType } from "next/navigation";

import { resolveRepoBySlug } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

export default async function LegacyRepoRequestsPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const token = (await getSessionToken()) ?? undefined;
  const ws = await resolveRepoBySlug(owner, repo, token);
  if (!ws) {
    redirect("/runs", RedirectType.replace);
  }
  redirect(
    `/runs?scope=repo&repo=${encodeURIComponent(ws.repo_id)}`,
    RedirectType.replace,
  );
}
