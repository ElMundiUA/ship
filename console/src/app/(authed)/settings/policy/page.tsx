/**
 * MIGRATED: /fleet/policy → /settings/policy per RFC-0010 P1-08.
 *
 * Workspace policies — prose standing rules (Workspace policy
 * injection). Plain markdown rules ("Always work via PR", "Never
 * commit secrets") that the backend prepends to the agent system
 * prompt at runtime — both in Navigator chat
 * (``TopicService.assemble_messages``) and in ``shipctl run``
 * stdout (``cli/lib/commands/run.mjs``).
 *
 * The list view is a server component; per-row enable/disable,
 * inline edit and delete live in the client island below.
 */
import Link from "next/link";
import { redirect } from "next/navigation";

import { PageBody, PageHeader } from "@/components/app-shell";
import { ButtonPrimary, Card, CardHeader } from "@/components/ui";
import {
  type ApiPolicy,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listPolicies,
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

import { PoliciesList } from "./policies-list";

export const dynamic = "force-dynamic";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function PoliciesPage({
  searchParams,
}: {
  searchParams?: SearchParams;
}) {
  const params = (await (searchParams ?? Promise.resolve({}))) ?? {};
  if (!isApiConfigured()) {
    return (
      <>
        <PageHeader title="Policies" kicker="settings" />
        <PageBody>
          <Card>
            <CardHeader
              title="Backend not configured"
              subtitle="Set SHIP_API_URL to manage workspace policies."
            />
          </Card>
        </PageBody>
      </>
    );
  }

  const token = await getCachedSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fpolicy");

  const workspaces = await getCachedWorkspaces();
  const resolved = await getResolvedWorkspaceId(params, workspaces);
  const workspace = pickWorkspace(workspaces, resolved);
  const multiWorkspace = workspaces.length > 1;

  let policies: ApiPolicy[];
  try {
    policies = await listPolicies(workspace.id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fsettings%2Fpolicy");
    }
    return renderUnavailable(err);
  }

  return (
    <>
      <PageHeader
        title="Policies"
        actions={
          <Link
            href={withWorkspaceQuery(
              "/settings/policy/new",
              workspace.id,
              multiWorkspace,
            )}
          >
            <ButtonPrimary>New policy</ButtonPrimary>
          </Link>
        }
      />
      <PageBody>
        <p className="mb-5 max-w-3xl text-xs text-white/55">
          Standing rules injected into the agent system prompt for
          every Navigator chat and every <code>shipctl run</code> in
          this workspace. Use them for invariants like &ldquo;Always
          work via PR&rdquo; or &ldquo;Never commit secrets&rdquo;.
          Disabled rules are kept around but not rendered into the
          prompt.
        </p>

        {policies.length === 0 ? (
          <Card>
            <CardHeader
              title="No policies yet"
              subtitle="Add one to enforce a standing rule across every agent run."
            />
            <div className="mt-3">
              <Link href="/settings/policy/new">
                <ButtonPrimary>New policy</ButtonPrimary>
              </Link>
            </div>
          </Card>
        ) : (
          <PoliciesList workspaceId={workspace.id} policies={policies} />
        )}
      </PageBody>
    </>
  );
}

function renderUnavailable(err: unknown) {
  const message =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : "Unknown error";
  return (
    <>
      <PageHeader title="Policies" kicker="settings" />
      <PageBody>
        <Card>
          <CardHeader
            title="Backend unreachable"
            subtitle={`Workspace policies are currently unavailable (${message}). Retry in a moment.`}
          />
        </Card>
      </PageBody>
    </>
  );
}
