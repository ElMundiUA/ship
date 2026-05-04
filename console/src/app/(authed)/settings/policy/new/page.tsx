/**
 * MIGRATED: /fleet/policy/new → /settings/policy/new per RFC-0010 P1-08.
 *
 * "New policy" form for the Workspace policy injection feature.
 *
 * Server wrapper resolves the workspace from the session bearer
 * (no per-policy permissions, member-level write); the client form
 * owns the title/body/sort/enabled fields and round-trips through
 * ``/api/policies``.
 */
import Link from "next/link";
import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiUnavailableError,
  isApiConfigured,
} from "@/lib/api/client";
import {
  getCachedSessionToken,
  getCachedWorkspaces,
} from "@/lib/api/session-cache.server";
import { getResolvedWorkspaceId } from "@/lib/workspace-resolve.server";
import {
  pickWorkspace,
  withWorkspaceQuery,
} from "@/lib/workspace-scope";

import { NewPolicyForm } from "./policy-form";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function NewPolicyPage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) ?? {};
  if (!isApiConfigured()) {
    return (
      <>
        <PageHeader title="New policy" kicker="settings" />
        <PageBody>
          <Card>
            <CardHeader
              title="Backend not configured"
              subtitle="Set SHIP_API_URL to create workspace policies."
            />
          </Card>
        </PageBody>
      </>
    );
  }

  const token = await getCachedSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fpolicy%2Fnew");

  let workspaces;
  try {
    workspaces = await getCachedWorkspaces();
  } catch (err) {
    return renderUnavailable(err);
  }
  const resolved = await getResolvedWorkspaceId(params, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);
  const multiWorkspace = workspaces.length > 1;

  return (
    <>
      <PageHeader
        title="New policy"
        actions={
          <Link
            href={withWorkspaceQuery(
              "/settings/policy",
              workspace.id,
              multiWorkspace,
            )}
            className="text-xs font-semibold text-white/65 hover:text-white"
          >
            ← Policies
          </Link>
        }
      />
      <PageBody>
        <NewPolicyForm workspaceId={workspace.id} />
      </PageBody>
    </>
  );
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <>
      <PageHeader title="New policy" kicker="settings" />
      <PageBody>
        <Card>
          <CardHeader
            title="Couldn't load the form"
            subtitle={
              isUnavailable
                ? "Backend is unreachable. Try again in a few seconds."
                : "Something went wrong."
            }
          />
        </Card>
      </PageBody>
    </>
  );
}
