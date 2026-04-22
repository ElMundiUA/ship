import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiFleetRequestCreateOut,
  ApiHttpError,
  ApiUnavailableError,
  getFleetRequest,
  isApiConfigured,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { FleetRequestDetail } from "./fleet-request-detail";

/**
 * Fleet Request detail (RFC-0008 §D).
 *
 * Renders the repo × status pivot for one parent fleet request plus
 * a cancel button (admin-only server-side). The pivot unifies three
 * surfaces into one view:
 *
 * - **Children** — one row per successfully-created
 *   :class:`AgentRequest`, with status badge + link to the GitHub
 *   Actions run when available.
 * - **Pre-flight rejections** — persisted on the parent's
 *   ``rejections`` column so a refresh still shows them.
 * - **Dispatch-time failures** — derived from child rows with
 *   ``status=dispatch_failed``.
 */

export const dynamic = "force-dynamic";

export default async function FleetRequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Fleet request" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to view fleet requests."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token)
    redirect(`/login?next=${encodeURIComponent(`/fleet/requests/${id}`)}`);

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(`/fleet/requests/${id}`)}`);
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let detail: ApiFleetRequestCreateOut;
  try {
    detail = await getFleetRequest(workspace.id, id, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 404) {
      notFound();
    }
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(`/fleet/requests/${id}`)}`);
    }
    return renderUnavailable(err);
  }

  const { fleet_request: parent } = detail;
  const title =
    parent.title ||
    parent.pattern_id ||
    parent.agent_slug ||
    "(untitled fleet request)";

  return (
    <AppShell
      title={title}
      kicker="fleet request"
      actions={
        <Link
          href="/fleet/requests"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Fleet requests
        </Link>
      }
    >
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Badge tone={statusTone(parent.status)} dot>
          {parent.status}
        </Badge>
        {parent.pattern_id ? (
          <span className="font-mono text-xs text-white/60">
            pattern: {parent.pattern_id}
          </span>
        ) : null}
        {parent.agent_slug ? (
          <span className="font-mono text-xs text-white/60">
            agent: {parent.agent_slug}
          </span>
        ) : null}
        <span className="font-mono text-xs text-white/60">
          {parent.dispatched_count}/{parent.target_count} dispatched
        </span>
        {parent.rejected_count > 0 ? (
          <span className="font-mono text-xs text-coral">
            {parent.rejected_count} rejected
          </span>
        ) : null}
      </div>

      <FleetRequestDetail workspaceId={workspace.id} detail={detail} />
    </AppShell>
  );
}

function statusTone(status: string): "ok" | "warn" | "err" | "neutral" | "info" {
  switch (status) {
    case "dispatched":
      return "ok";
    case "partial":
      return "warn";
    case "failed":
      return "err";
    case "dispatching":
      return "info";
    case "cancel_requested":
    case "cancelled":
      return "neutral";
    default:
      return "neutral";
  }
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Fleet request" kicker="fleet">
      <Card>
        <CardHeader
          title="Couldn't load this fleet request"
          subtitle={
            isUnavailable
              ? "Backend is unreachable. Try again in a few seconds."
              : "Something went wrong."
          }
        />
      </Card>
    </AppShell>
  );
}
