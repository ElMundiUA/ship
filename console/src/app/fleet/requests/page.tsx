import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader, ButtonPrimary } from "@/components/ui";
import {
  type ApiFleetRequest,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listFleetRequests,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Fleet Requests — list view (RFC-0008 §D).
 *
 * Workspace-level landing surface for "fan one catalog pattern out
 * across N repos" dispatches. The per-repo ``/requests`` page still
 * owns single-repo dispatch; this page lists every *fleet* dispatch
 * (parent row from ``fleet_requests``) newest first with a single
 * ``New fleet request`` entry point.
 *
 * Each row summarises {title, pattern, status, dispatched/target,
 * created_at} and links to the detail view where the repo × status
 * pivot lives.
 */

export const dynamic = "force-dynamic";

export default async function FleetRequestsPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Fleet requests" kicker="fleet">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire fleet requests."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Ffleet%2Frequests");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Frequests");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let fleetRequests: ApiFleetRequest[] = [];
  try {
    fleetRequests = await listFleetRequests(workspace.id, {
      token,
      limit: 50,
    });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Ffleet%2Frequests");
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Fleet requests"
      kicker="fleet"
      actions={
        <Link href="/fleet/requests/new">
          <ButtonPrimary>+ New fleet request</ButtonPrimary>
        </Link>
      }
    >
      <p className="mb-5 max-w-3xl text-xs text-white/55">
        Fan one catalog pattern out across many repos at once. Per-repo
        dispatches live on each repo&apos;s{" "}
        <Link href="/" className="text-aqua hover:underline">
          repo page
        </Link>
        . Fleet dispatches are best-effort: repos that fail pre-flight
        (missing GitHub App, etc.) are surfaced on the detail view
        without blocking the rest.
      </p>

      {fleetRequests.length === 0 ? (
        <Card>
          <CardHeader
            title="No fleet requests yet"
            subtitle="Click 'New fleet request' to fan a pattern out across repos."
          />
        </Card>
      ) : (
        <Card padded={false}>
          <ul className="divide-y divide-white/[0.08]">
            {fleetRequests.map((req) => (
              <FleetRequestRow key={req.id} request={req} />
            ))}
          </ul>
        </Card>
      )}
    </AppShell>
  );
}

function FleetRequestRow({ request }: { request: ApiFleetRequest }) {
  const title =
    request.title ||
    request.pattern_id ||
    request.agent_slug ||
    "(untitled fleet request)";
  return (
    <li>
      <Link
        href={`/fleet/requests/${encodeURIComponent(request.id)}`}
        className="block px-5 py-3.5 transition hover:bg-white/[0.03]"
      >
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-sm font-semibold text-white">
                {title}
              </span>
              <Badge tone={statusTone(request.status)} dot>
                {request.status}
              </Badge>
              {request.pattern_id ? (
                <span className="font-mono text-[11px] text-white/55">
                  {request.pattern_id}
                </span>
              ) : null}
            </div>
            <p className="mt-1 text-[11px] text-white/55">
              <span className="font-mono">
                {request.dispatched_count}/{request.target_count} dispatched
              </span>
              {request.rejected_count > 0 ? (
                <>
                  {" · "}
                  <span className="font-mono text-coral">
                    {request.rejected_count} rejected
                  </span>
                </>
              ) : null}
              {request.requested_by_email ? (
                <>
                  {" · "}
                  <span>{request.requested_by_email}</span>
                </>
              ) : null}
              {" · "}
              <span className="font-mono">
                {formatRelative(request.created_at)}
              </span>
            </p>
          </div>
          <span className="text-white/30">→</span>
        </div>
      </Link>
    </li>
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

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

function renderUnavailable(err: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Fleet requests" kicker="fleet">
      <Card>
        <CardHeader
          title="Couldn't load fleet requests"
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
