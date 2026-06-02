import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import { pickWorkspace } from "@/lib/workspace-scope";

import DeploymentsClient from "./deployments-client";

export const dynamic = "force-dynamic";

export default async function DeploymentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const token = await getCachedSessionToken();
  if (!token) redirect("/login?reason=session_expired");

  const workspaces = await getCachedWorkspaces().catch(() => null);
  if (!workspaces?.length) redirect("/onboarding?step=github");

  const search = await searchParams;
  const resolvedId = await getResolvedWorkspaceId(search, workspaces);
  const workspace = pickWorkspace(workspaces, resolvedId);

  return (
    <>
      <PageHeader title="Deployments" kicker={workspace.name} />
      <PageBody>
        <DeploymentsClient workspaceId={workspace.id} />
      </PageBody>
    </>
  );
}
