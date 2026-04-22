import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ImprovementRow } from "@/components/improvement-row";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiImprovement,
  type ApiImprovementDecision,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listImprovements,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode Improvements surface
 * (``/r/<owner>/<repo>/improvements``).
 *
 * Backend filter (``?repo_id=``) narrows the list server-side so the
 * page doesn't carry any ``ScopePill`` / ``?scope=`` baggage —
 * scope is implicit from the URL. Tabs still switch between
 * pending / accepted / declined / later.
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

export default async function RepoImprovementsPage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams: Promise<{
    decision?: string;
    banner?: string;
    focus?: string;
  }>;
}) {
  const [resolved, sp] = await Promise.all([params, searchParams]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const here = `/r/${slug}/improvements`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Improvements" kicker={`${slug} · repo`}>
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
  if (!token) redirect(`/login?next=${encodeURIComponent(here)}`);

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(here)}`);
  }
  if (result.kind === "down") return renderUnavailable();
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;

  const decisionFilter: ApiImprovementDecision = VALID_DECISIONS.includes(
    sp.decision as ApiImprovementDecision,
  )
    ? (sp.decision as ApiImprovementDecision)
    : "pending";

  let allRows: ApiImprovement[] = [];
  try {
    allRows = await listImprovements(ctx.workspace.id, {
      token,
      repoId: ctx.repo.id,
    });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${encodeURIComponent(here)}`);
    }
    return renderUnavailable(err);
  }

  const counts: Record<string, number> = {
    pending: 0,
    accepted: 0,
    declined: 0,
    deferred: 0,
    total: allRows.length,
  };
  for (const r of allRows) counts[r.decision] = (counts[r.decision] ?? 0) + 1;

  const rows = allRows
    .filter((r) => r.decision === decisionFilter)
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );

  const banner = pickBanner(sp.banner);

  return (
    <AppShell
      title="Improvements"
      kicker={`${ctx.repo.full_name} · repo`}
      workspace={{
        id: ctx.workspace.id,
        name: ctx.workspace.name,
        slug: ctx.workspace.slug,
      }}
      scope={{
        repos: ctx.repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: ctx.repo.id,
      }}
      actions={
        <Link
          href={`/r/${ctx.repo.full_name}`}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← Repo
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Agent-proposed changes for{" "}
        <span className="font-mono text-white/75">{ctx.repo.full_name}</span>:
        refactors, doc gaps, test coverage, architecture nudges.
        Accept to kick off an open-PR lane; decline with a reason so
        we stop re-suggesting it.
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

      <div className="mb-5 flex flex-wrap gap-2 border-b border-white/10 pb-2">
        {(
          [
            { key: "pending", label: "Pending" },
            { key: "accepted", label: "Accepted" },
            { key: "declined", label: "Declined" },
            { key: "deferred", label: "Later" },
          ] as const
        ).map((tab) => (
          <Link
            key={tab.key}
            href={tab.key === "pending" ? here : `${here}?decision=${tab.key}`}
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
          {counts.total} total in {ctx.repo.full_name}
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
              workspaceId={ctx.workspace.id}
              repoName={ctx.repo.full_name}
              decisionFilter={decisionFilter}
              focused={sp.focus === row.id}
            />
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function EmptyState({
  decisionFilter,
}: {
  decisionFilter: ApiImprovementDecision;
}) {
  const copy: Record<ApiImprovementDecision, string> = {
    pending:
      "Nothing pending for this repo — the agent hasn't surfaced any new proposals since the last sweep.",
    accepted: "No accepted proposals for this repo yet.",
    declined: "No declined proposals for this repo yet.",
    deferred: "No deferred proposals for this repo yet.",
  };
  return (
    <Card>
      <p className="text-sm text-white/70">{copy[decisionFilter]}</p>
    </Card>
  );
}

function renderUnavailable(err?: unknown) {
  const msg =
    err instanceof ApiUnavailableError
      ? err.message
      : err instanceof Error
        ? err.message
        : "";
  return (
    <AppShell title="Improvements">
      <Card>
        <CardHeader
          title="Backend unavailable"
          subtitle="The console couldn't reach the Ship API. Retry in a moment."
        />
        {msg ? (
          <p className="mt-2 font-mono text-[11px] text-rose-300">{msg}</p>
        ) : null}
      </Card>
    </AppShell>
  );
}
