import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiActivatedRepo,
  type ApiAgentRequest,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listAgentRequests,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { NewRequestForm } from "./new-request";

/**
 * Requests — one-shot agent runs.
 *
 * The counterpart to ``/lanes``: ``/lanes`` edits the recurring /
 * trigger-driven side of ``.ship/config.yml``; ``/requests`` fires
 * ad-hoc agent runs (BA, QA, architect review) against a concrete
 * context (ticket / PR / file path). These intentionally do **not**
 * live in ``.ship/config.yml`` — a one-shot with inputs has no
 * business being a cron entry.
 *
 * The server component fetches recent requests + the activated-repo
 * list; the ``NewRequestForm`` client component handles dispatch.
 */

export const dynamic = "force-dynamic";

export default async function RequestsPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Requests">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to wire one-shot agent runs."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Frequests");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Frequests");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  let repos: ApiActivatedRepo[] = [];
  let requests: ApiAgentRequest[] = [];
  try {
    [repos, requests] = await Promise.all([
      listActivatedRepos(workspace.id, token).catch(
        () => [] as ApiActivatedRepo[],
      ),
      listAgentRequests(workspace.id, { token, limit: 25 }).catch(
        () => [] as ApiAgentRequest[],
      ),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Frequests");
    }
    return renderUnavailable(err);
  }

  const sortedRepos = [...repos].sort((a, b) =>
    a.full_name.localeCompare(b.full_name),
  );

  return (
    <AppShell
      title="Requests"
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      scope={{
        repos: sortedRepos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: sortedRepos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/lanes"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Lanes
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Pick an agent, attach a piece of context (ticket, PR, file
        path, free-form description), drop a prompt — we dispatch the
        workflow once and track the run here. Unlike{" "}
        <Link href="/lanes" className="text-aqua hover:underline">
          lanes
        </Link>
        , requests don&apos;t land in{" "}
        <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
          .ship/config.yml
        </code>{" "}
        — they&apos;re one-shot dispatches.
      </p>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
        <div className="lg:col-span-3">
          <NewRequestForm workspaceId={workspace.id} repos={sortedRepos} />
        </div>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Recent requests"
            subtitle="Newest first. Click any row for the GitHub Actions run."
          />
          {requests.length === 0 ? (
            <div className="mt-5 rounded-lg border border-dashed border-white/10 bg-white/[0.02] p-6 text-center text-xs text-white/55">
              <p>Nothing dispatched yet.</p>
              <p className="mt-1 text-[11px] text-white/40">
                Fire your first one on the left — it&apos;ll show up
                here with a link to the Actions run.
              </p>
            </div>
          ) : (
            <ul className="mt-4 space-y-2">
              {requests.map((r) => (
                <RequestRow key={r.id} request={r} />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </AppShell>
  );
}

function RequestRow({ request }: { request: ApiAgentRequest }) {
  const body = (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone="info">{request.agent_slug}</Badge>
        <Badge tone={statusTone(request.status)} dot>
          {request.status}
        </Badge>
        <span className="font-mono text-[10px] text-white/45">
          {formatRelative(request.created_at)}
        </span>
      </div>
      <p className="mt-1 font-mono text-[11px] text-white/55">
        {request.repo_full_name}
      </p>
      <p className="mt-1 line-clamp-2 text-[12px] text-white/75">
        {request.prompt}
      </p>
      {request.context_ref ? (
        <p className="mt-1 truncate font-mono text-[10px] text-white/45">
          ctx: {request.context_ref}
        </p>
      ) : null}
      {request.summary && request.status === "dispatch_failed" ? (
        <p className="mt-1 text-[10px] text-coral">{request.summary}</p>
      ) : null}
    </>
  );
  const className =
    "block rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2 transition hover:border-white/25 hover:bg-white/[0.04]";

  if (request.gh_html_url) {
    return (
      <li>
        <a
          href={request.gh_html_url}
          target="_blank"
          rel="noreferrer"
          className={className}
        >
          {body}
        </a>
      </li>
    );
  }
  return (
    <li className={className}>
      {body}
    </li>
  );
}

function statusTone(status: string): "ok" | "warn" | "err" | "neutral" | "info" {
  switch (status) {
    case "succeeded":
      return "ok";
    case "failed":
    case "dispatch_failed":
      return "err";
    case "dispatching":
      return "warn";
    case "dispatched":
      return "info";
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
    <AppShell title="Requests">
      <Card>
        <CardHeader
          title="Couldn't load the Requests surface"
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
