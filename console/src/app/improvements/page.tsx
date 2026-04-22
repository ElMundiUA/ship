import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ImprovementRow } from "@/components/improvement-row";
import { ScopePill } from "@/components/scope-pill";
import {
  type ResolvedScope,
  resolveScopeFromSearch,
} from "@/lib/scope";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiImprovement,
  type ApiImprovementDecision,
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listImprovements,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Improvements page (C8).
 *
 * The "what does the agent think we should change?" inbox. Every
 * row is a proposal (refactor / doc / test / arch nudge). The user
 * clicks *accept* / *decline* / *later* and Ship feeds the decision
 * back to the agent so future scans don't re-propose rejected work.
 *
 * Tabs by decision bucket. Cards group by repo within each tab so
 * the reviewer sees "everything Ship wants to change about repo X"
 * together.
 */

export const dynamic = "force-dynamic";

const VALID_DECISIONS: readonly ApiImprovementDecision[] = [
  "pending",
  "accepted",
  "declined",
  "deferred",
];

type BannerKind = { tone: "ok" | "warn" | "err"; text: string };

function pickBanner(param: string | undefined): BannerKind | null {
  if (!param) return null;
  if (param.startsWith("decided_")) {
    const d = param.slice("decided_".length);
    return { tone: "ok", text: `Marked as ${d}.` };
  }
  switch (param) {
    case "reason_required":
      return {
        tone: "warn",
        text: "Declining requires a short reason (the agent learns from it).",
      };
    case "not_found":
      return { tone: "warn", text: "Improvement no longer exists." };
    case "bad_input":
      return { tone: "warn", text: "Invalid input." };
    case "api_unavailable":
      return {
        tone: "err",
        text: "Backend unreachable — try again in a moment.",
      };
    default:
      if (param.startsWith("http_")) {
        return { tone: "err", text: `Backend returned ${param.slice(5)}.` };
      }
      return { tone: "err", text: "Something went sideways." };
  }
}

export default async function ImprovementsPage({
  searchParams,
}: {
  searchParams: Promise<{
    decision?: string;
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
      <AppShell title="Improvements">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load the improvements surface."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fimprovements");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Fimprovements");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];
  const decisionFilter: ApiImprovementDecision = VALID_DECISIONS.includes(
    params.decision as ApiImprovementDecision,
  )
    ? (params.decision as ApiImprovementDecision)
    : "pending";

  let allRows: ApiImprovement[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  let me: Awaited<ReturnType<typeof getMe>> | null = null;
  try {
    [allRows, repos, me] = await Promise.all([
      listImprovements(workspace.id, { token }),
      listActivatedRepos(workspace.id, token).catch(() => []),
      getMe(token).catch(() => null),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fimprovements");
    return renderUnavailable(err);
  }

  // Phase 4b: repo scope filter is client-side — the backend
  // improvements list only accepts ``?decision=`` today. User
  // scope surfaces a banner and falls back to the full list.
  const scopedRows =
    scope.kind === "repo" && scope.repoId
      ? allRows.filter((r) => r.repo_id === scope.repoId)
      : allRows;

  const counts: Record<string, number> = {
    pending: 0,
    accepted: 0,
    declined: 0,
    deferred: 0,
    total: scopedRows.length,
  };
  for (const r of scopedRows) counts[r.decision] = (counts[r.decision] ?? 0) + 1;

  const rows = scopedRows
    .filter((r) => r.decision === decisionFilter)
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
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
      title="Improvements"
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
        Agent-proposed changes: refactors, doc gaps, test coverage,
        architecture nudges. Accept to kick off an open-PR lane;
        decline with a reason so we stop re-suggesting it.
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
          User scope doesn&apos;t filter improvements yet — we don&apos;t
          track &ldquo;assigned to me&rdquo; for proposals. Pick a repo
          scope to narrow the list.
        </div>
      ) : null}

      <div className="mb-5 flex flex-wrap gap-2 border-b border-white/10 pb-2">
        {([
          { key: "pending", label: "Pending" },
          { key: "accepted", label: "Accepted" },
          { key: "declined", label: "Declined" },
          { key: "deferred", label: "Later" },
        ] as const).map((tab) => (
          <Link
            key={tab.key}
            href={tabHref("/improvements", { decision: tab.key, scope })}
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
              decisionFilter === tab.key
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
        <EmptyState decisionFilter={decisionFilter} />
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <ImprovementRow
              key={row.id}
              row={row}
              workspaceId={workspace.id}
              repoName={row.repo_id ? reposById.get(row.repo_id)?.full_name ?? null : null}
              decisionFilter={decisionFilter}
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
  opts: { decision: ApiImprovementDecision; scope: ResolvedScope },
): string {
  const qs = new URLSearchParams();
  if (opts.decision !== "pending") qs.set("decision", opts.decision);
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

function EmptyState({
  decisionFilter,
}: {
  decisionFilter: ApiImprovementDecision;
}) {
  const copy: Record<ApiImprovementDecision, string> = {
    pending:
      "Nothing pending — the agent hasn't surfaced any new proposals since the last sweep.",
    accepted: "No accepted proposals yet. Approving any pending row lands it here.",
    declined: "No declined proposals yet.",
    deferred: "No deferred proposals yet.",
  };
  return (
    <Card>
      <p className="text-sm text-white/70">{copy[decisionFilter]}</p>
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
    <AppShell title="Improvements">
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
