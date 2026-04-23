/**
 * MIGRATED: /r/[owner]/[repo]/lanes/[laneRowId] → /automations/<laneRowId>?scope=repo&repo=<id> per RFC-0010 P1-07.
 *
 * Lane detail moves to the workspace ``/automations/[id]`` route;
 * the per-repo URL keeps working as a 308 so deep links survive.
 * The ``laneRowId`` is preserved 1:1 (it's the global Lane row id,
 * not repo-scoped) and the ``?scope=repo&repo=<id>`` query nudges
 * the new page to render with the repo scope chip pre-selected.
 */
import { redirect, RedirectType } from "next/navigation";

import { resolveRepoBySlug } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

export default async function LegacyRepoLaneDetailPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string; laneRowId: string }>;
}) {
  const { owner, repo, laneRowId } = await params;
  const token = (await getSessionToken()) ?? undefined;
  const ws = await resolveRepoBySlug(owner, repo, token);
  const idPart = encodeURIComponent(laneRowId);
  if (!ws) {
    redirect(`/automations/${idPart}`, RedirectType.replace);
  }
  redirect(
    `/automations/${idPart}?scope=repo&repo=${encodeURIComponent(ws.repo_id)}`,
    RedirectType.replace,
  );
}
