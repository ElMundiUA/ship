/**
 * MIGRATED: /r/[owner]/[repo]/lanes → /automations?scope=repo&repo=<id> per RFC-0010 P1-07.
 *
 * Per-repo lanes page collapsed into the workspace
 * ``/automations`` surface with a ``scope=repo`` chip. The slug →
 * repo_id resolution happens server-side here so the redirect URL
 * is canonical. Falls back to the unscoped page if the slug
 * doesn't match anything (mistyped URL, decommissioned repo).
 */
import { redirect, RedirectType } from "next/navigation";

import { resolveRepoBySlug } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export const dynamic = "force-dynamic";

export default async function LegacyRepoLanesPage({
  params,
}: {
  params: Promise<{ owner: string; repo: string }>;
}) {
  const { owner, repo } = await params;
  const token = (await getSessionToken()) ?? undefined;
  const ws = await resolveRepoBySlug(owner, repo, token);
  if (!ws) {
    redirect("/automations", RedirectType.replace);
  }
  redirect(
    `/automations?scope=repo&repo=${encodeURIComponent(ws.repo_id)}`,
    RedirectType.replace,
  );
}
