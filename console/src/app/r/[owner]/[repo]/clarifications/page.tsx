import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ClarificationRow } from "@/components/clarification-row";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiClarification,
  type ApiClarificationStatus,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listClarifications,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-mode Clarifications inbox
 * (``/r/<owner>/<repo>/clarifications``).
 *
 * Backend filter (``?repo_id=``) does the narrowing, so there's no
 * ``ScopePill`` / ``?scope=`` / ``?project_id=`` ceremony — the
 * scope is literally the URL. Tabs still switch between
 * open / answered / skipped.
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

export default async function RepoClarificationsPage({
  params,
  searchParams,
}: {
  params: Promise<RepoRouteParams>;
  searchParams: Promise<{
    status?: string;
    banner?: string;
    focus?: string;
  }>;
}) {
  const [resolved, sp] = await Promise.all([params, searchParams]);
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const here = `/r/${slug}/clarifications`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Clarifications" kicker={`${slug} · repo`}>
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
  if (!token) redirect(`/login?next=${encodeURIComponent(here)}`);

  const result = await resolveRepoContext(token, slug);
  if (result.kind === "unauthorized") {
    redirect(`/login?next=${encodeURIComponent(here)}`);
  }
  if (result.kind === "down") return renderUnavailable();
  if (result.kind === "empty") redirect("/onboarding?step=github");
  if (result.kind === "not-found") notFound();

  const ctx = result.ctx;

  const statusFilter: ApiClarificationStatus = VALID_STATUSES.includes(
    sp.status as ApiClarificationStatus,
  )
    ? (sp.status as ApiClarificationStatus)
    : "open";

  let allRows: ApiClarification[] = [];
  try {
    allRows = await listClarifications(ctx.workspace.id, {
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
    open: 0,
    answered: 0,
    skipped: 0,
    stale: 0,
    total: allRows.length,
  };
  for (const r of allRows) counts[r.status] = (counts[r.status] ?? 0) + 1;

  const rows = allRows
    .filter((r) => r.status === statusFilter)
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );

  const banner = pickBanner(sp.banner);

  return (
    <AppShell
      title="Clarifications"
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
        Everything the agent is waiting on a human for in{" "}
        <span className="font-mono text-white/75">{ctx.repo.full_name}</span>.
        Answer inline or mark as &ldquo;not relevant&rdquo; to clear the queue.
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
            { key: "open", label: "Open" },
            { key: "answered", label: "Answered" },
            { key: "skipped", label: "Skipped" },
          ] as const
        ).map((tab) => (
          <Link
            key={tab.key}
            href={tab.key === "open" ? here : `${here}?status=${tab.key}`}
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
          {counts.total} total in {ctx.repo.full_name}
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
              workspaceId={ctx.workspace.id}
              repoName={ctx.repo.full_name}
              statusFilter={statusFilter}
              focused={sp.focus === row.id}
            />
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function EmptyState({ statusFilter }: { statusFilter: ApiClarificationStatus }) {
  const copy =
    statusFilter === "open"
      ? "Queue is empty — the agent isn't blocked on anything from this repo right now."
      : statusFilter === "answered"
        ? "No answered clarifications for this repo yet."
        : "No skipped clarifications for this repo yet.";
  return (
    <Card>
      <p className="text-sm text-white/70">{copy}</p>
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
    <AppShell title="Clarifications">
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
