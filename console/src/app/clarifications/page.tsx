import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ClarificationRow } from "@/components/clarification-row";
import { ScopePill } from "@/components/scope-pill";
import {
  type ResolvedScope,
  resolveScopeFromSearch,
} from "@/lib/scope";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiClarification,
  type ApiClarificationStatus,
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listClarifications,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Clarifications inbox (C9).
 *
 * One page per workspace where every agent-raised question about a
 * ticket / repo / PR lives until a human answers or marks it
 * "skipped". Populated either by a pipeline run's callback (the
 * happy path) or by an admin manually via the API. Answers flow
 * back to the originating pipeline's audit log for the retro.
 *
 * Tabs: ``open`` / ``answered`` / ``skipped`` — default is ``open``
 * because that's where the work is. Counts live in the tab labels
 * so we can see the backlog without loading each bucket.
 */

export const dynamic = "force-dynamic";

const VALID_STATUSES: readonly ApiClarificationStatus[] = [
  "open",
  "answered",
  "skipped",
];

type BannerKind = { tone: "ok" | "warn" | "err"; text: string };

function pickBanner(param: string | undefined): BannerKind | null {
  switch (param) {
    case "answered":
      return { tone: "ok", text: "Answer recorded." };
    case "skipped":
      return { tone: "ok", text: "Marked as not relevant." };
    case "reopened":
      return { tone: "ok", text: "Clarification reopened." };
    case "empty_answer":
      return { tone: "warn", text: "Answer body can't be empty." };
    case "not_found":
      return { tone: "warn", text: "Clarification no longer exists." };
    case "bad_input":
      return { tone: "warn", text: "Invalid input." };
    case "api_unavailable":
      return {
        tone: "err",
        text: "Backend unreachable — try again in a moment.",
      };
    case undefined:
      return null;
    default:
      if (param?.startsWith("http_")) {
        return { tone: "err", text: `Backend returned ${param.slice(5)}.` };
      }
      return { tone: "err", text: "Something went sideways." };
  }
}

export default async function ClarificationsPage({
  searchParams,
}: {
  searchParams: Promise<{
    status?: string;
    banner?: string;
    focus?: string;
    scope?: string;
    repo_id?: string;
    project_id?: string;
  }>;
}) {
  const params = await searchParams;
  const scope = resolveScopeFromSearch(params);
  if (!isApiConfigured()) {
    return (
      <AppShell title="Clarifications">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load the clarifications inbox."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fclarifications");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fclarifications");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  const statusFilter: ApiClarificationStatus =
    VALID_STATUSES.includes(params.status as ApiClarificationStatus)
      ? (params.status as ApiClarificationStatus)
      : "open";

  // Load every status at once so we can render tab counts. Cheap —
  // one SELECT per tab, all filtered on the same index.
  let allRows: ApiClarification[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  let me: Awaited<ReturnType<typeof getMe>> | null = null;
  try {
    [allRows, repos, me] = await Promise.all([
      listClarifications(workspace.id, { token }),
      listActivatedRepos(workspace.id, token).catch(() => []),
      getMe(token).catch(() => null),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fclarifications");
    return renderUnavailable(err);
  }

  // Phase 4b: scope filter runs client-side because the backend
  // ``GET /v1/workspaces/{ws}/clarifications`` only accepts
  // ``?status=`` today. Rows carry ``repo_id`` already so we just
  // drop non-matching ones. ``user`` scope has no analog on this
  // surface (no "assigned to me" concept) so we surface a banner
  // and keep the full list — cheap to answer from any tab.
  const scopedRows =
    scope.kind === "repo" && scope.repoId
      ? allRows.filter((r) => r.repo_id === scope.repoId)
      : allRows;

  const counts = {
    open: 0,
    answered: 0,
    skipped: 0,
    stale: 0,
    total: scopedRows.length,
  } as Record<string, number>;
  for (const r of scopedRows) counts[r.status] = (counts[r.status] ?? 0) + 1;

  const rows = scopedRows.filter((r) => r.status === statusFilter);
  rows.sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  );

  const reposById = new Map(repos.map((r) => [r.id, r]));
  const banner = pickBanner(params.banner);

  const scopePill = (
    <ScopePill
      workspaceName={workspace.name}
      repos={repos.map((r) => ({ id: r.id, full_name: r.full_name }))}
      me={
        me
          ? { id: me.id, email: me.email, display_name: me.display_name }
          : null
      }
    />
  );

  return (
    <AppShell
      title="Clarifications"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: scope.kind === "repo" ? scope.repoId : repos[0]?.id ?? null,
      }}
      scopePill={scopePill}
      actions={
        <Link
          href="/"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Dashboard
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Everything the agent is waiting on a human for — usually the
        missing context that lets a ticket get auto-resolved. Answer
        inline or mark as &ldquo;not relevant&rdquo; to clear the queue.
      </p>

      {banner ? (
        <div
          className={`mb-4 rounded-lg border px-3 py-2 text-[12px] ${
            banner.tone === "ok"
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-200"
              : banner.tone === "warn"
                ? "border-amber-500/30 bg-amber-500/10 text-amber-200"
                : "border-rose-500/30 bg-rose-500/10 text-rose-200"
          }`}
        >
          {banner.text}
        </div>
      ) : null}

      {scope.kind === "user" ? (
        <div className="mb-4 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12px] text-amber-100/85">
          User scope doesn&apos;t filter clarifications yet — answer
          any open row from this list and the agent will route the
          reply to you. Switch to a repo scope to narrow the queue.
        </div>
      ) : null}

      <div className="mb-5 flex flex-wrap gap-2 border-b border-white/10 pb-2">
        {([
          { key: "open", label: "Open" },
          { key: "answered", label: "Answered" },
          { key: "skipped", label: "Skipped" },
        ] as const).map((tab) => (
          <Link
            key={tab.key}
            href={tabHref("/clarifications", { status: tab.key, scope })}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
              statusFilter === tab.key
                ? "bg-white/10 text-white"
                : "text-white/55 hover:text-white"
            }`}
          >
            {tab.label}
            <span className="ml-2 text-white/40">{counts[tab.key] ?? 0}</span>
          </Link>
        ))}
        <span className="ml-auto text-[11px] text-white/40">
          {counts.total} total in {scopeLabel(scope, repos, workspace.name)}
        </span>
      </div>

      {rows.length === 0 ? (
        <EmptyState statusFilter={statusFilter} />
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <ClarificationRow
              key={row.id}
              row={row}
              workspaceId={workspace.id}
              repoName={row.repo_id ? reposById.get(row.repo_id)?.full_name ?? null : null}
              statusFilter={statusFilter}
              focused={params.focus === row.id}
            />
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function tabHref(
  base: string,
  opts: { status: ApiClarificationStatus; scope: ResolvedScope },
): string {
  const qs = new URLSearchParams();
  if (opts.status !== "open") qs.set("status", opts.status);
  if (opts.scope.kind !== "workspace") {
    qs.set("scope", opts.scope.kind);
    if (opts.scope.kind === "repo" && opts.scope.repoId) {
      qs.set("repo_id", opts.scope.repoId);
    }
  }
  const suffix = qs.toString();
  return suffix ? `${base}?${suffix}` : base;
}

function scopeLabel(
  scope: ResolvedScope,
  repos: { id: string; full_name: string }[],
  workspaceName: string,
): string {
  if (scope.kind === "repo" && scope.repoId) {
    const r = repos.find((x) => x.id === scope.repoId);
    return r ? r.full_name : "selected repo";
  }
  if (scope.kind === "user") return "your queue";
  return `${workspaceName} workspace`;
}

function EmptyState({ statusFilter }: { statusFilter: ApiClarificationStatus }) {
  const copy =
    statusFilter === "open"
      ? "Queue is empty — the agent isn't blocked on anything right now."
      : statusFilter === "answered"
        ? "No answered clarifications yet. Once you answer one it shows up here."
        : "No skipped clarifications yet.";
  return (
    <Card>
      <p className="text-sm text-white/70">{copy}</p>
    </Card>
  );
}

function renderUnavailable(err: unknown) {
  const msg =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : String(err);
  return (
    <AppShell title="Clarifications">
      <Card>
        <CardHeader
          title="Backend unavailable"
          subtitle="The console couldn't reach the Ship API. Retry in a moment."
        />
        <p className="mt-2 font-mono text-[11px] text-rose-300">{msg}</p>
      </Card>
    </AppShell>
  );
}
