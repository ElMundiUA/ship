import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import {
  type ResolvedScope,
  ScopePill,
  resolveScopeFromSearch,
} from "@/components/scope-pill";
import { Badge, Card, CardHeader } from "@/components/ui";
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

function ImprovementRow({
  row,
  workspaceId,
  repoName,
  decisionFilter,
  focused,
}: {
  row: ApiImprovement;
  workspaceId: string;
  repoName: string | null;
  decisionFilter: ApiImprovementDecision;
  focused: boolean;
}) {
  const created = new Date(row.created_at);
  const contextKeys = Object.keys(row.context || {});
  return (
    <li
      id={`imp-${row.id}`}
      className={`rounded-xl border px-4 py-4 transition ${
        focused
          ? "border-aqua/40 bg-aqua/5"
          : "border-white/10 bg-white/[0.02]"
      }`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-[11px] text-white/45">
            <Badge>{row.kind}</Badge>
            {repoName ? <span>{repoName}</span> : null}
            <span>{created.toLocaleString()}</span>
            {row.impact ? (
              <span className="text-white/55">impact: {row.impact}</span>
            ) : null}
            {row.effort ? (
              <span className="text-white/55">effort: {row.effort}</span>
            ) : null}
            {row.pipeline_run_id ? (
              <span>
                from run{" "}
                <span className="font-mono text-white/55">
                  {row.pipeline_run_id.slice(0, 8)}
                </span>
              </span>
            ) : null}
          </div>
          <h3 className="mt-1 font-semibold text-white">{row.title}</h3>
          <p className="mt-1 whitespace-pre-wrap text-sm text-white/80">
            {row.body}
          </p>
          {contextKeys.length > 0 ? (
            <details className="mt-2">
              <summary className="cursor-pointer text-[11px] text-white/45 hover:text-white/70">
                context ({contextKeys.length})
              </summary>
              <pre className="mt-2 overflow-x-auto rounded bg-black/30 p-2 text-[11px] text-white/70">
                {JSON.stringify(row.context, null, 2)}
              </pre>
            </details>
          ) : null}
        </div>
      </div>

      {row.decision !== "pending" ? (
        <div
          className={`mt-3 rounded-lg p-3 text-[12px] ${
            row.decision === "accepted"
              ? "bg-emerald-500/5 text-emerald-100"
              : row.decision === "declined"
                ? "bg-rose-500/5 text-rose-100"
                : "bg-amber-500/5 text-amber-100"
          }`}
        >
          <div className="mb-1 text-[10px] uppercase tracking-wider opacity-70">
            {row.decision}
            {row.decided_by_email ? <> · {row.decided_by_email}</> : null}
            {row.decided_at
              ? ` · ${new Date(row.decided_at).toLocaleString()}`
              : ""}
          </div>
          {row.decision_reason ? (
            <div className="whitespace-pre-wrap">{row.decision_reason}</div>
          ) : null}
          {row.next_action_url ? (
            <a
              href={row.next_action_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2 inline-block text-[11px] font-semibold text-white/70 underline hover:text-white"
            >
              Open follow-up →
            </a>
          ) : null}
        </div>
      ) : null}

      {decisionFilter === "pending" ? (
        <DecisionForms workspaceId={workspaceId} row={row} />
      ) : (
        <form action="/api/improvements/decide" method="POST" className="mt-3">
          <input type="hidden" name="ws" value={workspaceId} />
          <input type="hidden" name="id" value={row.id} />
          <input type="hidden" name="decision" value="pending" />
          <input type="hidden" name="decision_filter" value={decisionFilter} />
          <button
            type="submit"
            className="text-[11px] font-semibold text-white/55 hover:text-white"
          >
            Undo decision
          </button>
        </form>
      )}
    </li>
  );
}

function DecisionForms({
  workspaceId,
  row,
}: {
  workspaceId: string;
  row: ApiImprovement;
}) {
  return (
    <div className="mt-3 flex flex-wrap gap-2">
      <form action="/api/improvements/decide" method="POST" className="contents">
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="id" value={row.id} />
        <input type="hidden" name="decision_filter" value="pending" />
        <button
          type="submit"
          name="decision"
          value="accepted"
          className="rounded-md bg-emerald-500 px-3 py-1.5 text-xs font-semibold text-black hover:bg-emerald-400"
        >
          Accept
        </button>
        <button
          type="submit"
          name="decision"
          value="deferred"
          className="rounded-md border border-white/10 px-3 py-1.5 text-xs font-semibold text-white/70 hover:bg-white/5"
        >
          Later
        </button>
      </form>
      <form
        action="/api/improvements/decide"
        method="POST"
        className="flex flex-wrap items-center gap-2"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        <input type="hidden" name="id" value={row.id} />
        <input type="hidden" name="decision_filter" value="pending" />
        <input
          type="text"
          name="reason"
          placeholder="why decline?"
          required
          className="rounded-md border border-white/10 bg-black/30 px-2 py-1.5 text-xs text-white placeholder-white/30 focus:border-rose-400 focus:outline-none"
        />
        <button
          type="submit"
          name="decision"
          value="declined"
          className="rounded-md border border-rose-500/30 px-3 py-1.5 text-xs font-semibold text-rose-200 hover:bg-rose-500/10"
        >
          Decline
        </button>
      </form>
    </div>
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
