import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { ScopePill } from "@/components/scope-pill";
import { Card, CardHeader } from "@/components/ui";
import {
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listClarifications,
  listImprovements,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Inbox v1 stub (RFC-0010 day-1 deliverable).
 *
 * The full Inbox shipping in Phase 2 (P2-12 / P2-13) is a unified
 * attention surface that absorbs clarifications, improvements,
 * failures, approvals, and exceptions into a single typed work queue
 * with routing, ownership, snooze, and audit trail. This page is the
 * **navigation entry point** that ships before the backend tables
 * exist, so design + product can iterate on the destination URL and
 * sidebar slot while engineering builds the data model
 * (P2-01 .. P2-09) underneath.
 *
 * What it shows today:
 *   - Live counts of the two existing source tables that the v1
 *     inbox will absorb (`clarifications` open + `improvements`
 *     pending) so the placeholder is not lying about workload.
 *   - Static preview of the five inbox types and the four canonical
 *     filters (`Mine` / `All` / `Unassigned` / type chips) that the
 *     Phase-2 list will adopt — purely visual, no routing yet.
 *   - Direct deeplinks to the legacy surfaces so operators that land
 *     here can still get their work done before the redirect from
 *     `/clarifications` and `/improvements` goes live in P2-17.
 *
 * Replace this file with the real list view once `P2-12 [FE]` lands.
 */

export const dynamic = "force-dynamic";

type SourceCounts = {
  clarifications_open: number;
  improvements_pending: number;
};

type InboxTypeMeta = {
  key: "clarification" | "improvement" | "failure" | "approval" | "exception";
  label: string;
  blurb: string;
  status: "live-source" | "phase-2";
  sourceHref?: string;
  count?: number;
};

export default async function InboxPage() {
  if (!isApiConfigured()) {
    return (
      <AppShell title="Inbox">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to load inbox previews."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Finbox");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect("/login?next=%2Finbox");
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");

  const workspace = workspaces[0];

  const [clarifications, improvements, repos, me] = await Promise.all([
    listClarifications(workspace.id, { token }).catch(() => []),
    listImprovements(workspace.id, { token }).catch(() => []),
    listActivatedRepos(workspace.id, token).catch(() => []),
    getMe(token).catch(() => null),
  ]);

  const counts: SourceCounts = {
    clarifications_open: clarifications.filter((r) => r.status === "open")
      .length,
    improvements_pending: improvements.filter((r) => r.decision === "pending")
      .length,
  };

  const total = counts.clarifications_open + counts.improvements_pending;

  const types: InboxTypeMeta[] = [
    {
      key: "clarification",
      label: "Clarifications",
      blurb: "Agent is blocked waiting on context from a human.",
      status: "live-source",
      sourceHref: "/clarifications",
      count: counts.clarifications_open,
    },
    {
      key: "improvement",
      label: "Improvements",
      blurb: "Proposed changes awaiting yes / no / later.",
      status: "live-source",
      sourceHref: "/improvements",
      count: counts.improvements_pending,
    },
    {
      key: "failure",
      label: "Failures",
      blurb: "Repeated automation failures requiring intervention.",
      status: "phase-2",
    },
    {
      key: "approval",
      label: "Approvals",
      blurb: "Pre-merge gates and risky actions awaiting sign-off.",
      status: "phase-2",
    },
    {
      key: "exception",
      label: "Exceptions",
      blurb: "Policy override / waiver requests from a run.",
      status: "phase-2",
    },
  ];

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
      title="Inbox"
      kicker="Preview"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repos[0]?.id ?? null,
      }}
      scopePill={scopePill}
      me={
        me
          ? {
              name: me.display_name ?? me.email,
              email: me.email,
              initials: initialsOf(me.display_name ?? me.email),
            }
          : undefined
      }
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
        One queue for everything that needs a human: clarifications,
        improvements, failures, approvals, exceptions. Single owner per
        item, typed dispositions, audit trail. The full list ships in
        the next sprint — for now this page surfaces the live sources
        and previews the destination shape.
      </p>

      <div className="mb-6 flex flex-wrap items-center gap-2 border-b border-white/10 pb-3">
        {(["Mine", "All", "Unassigned"] as const).map((tab, i) => (
          <button
            key={tab}
            type="button"
            disabled
            aria-disabled
            title="Filter ships in Phase 2"
            className={`rounded-md px-3 py-1.5 text-xs font-semibold transition ${
              i === 0
                ? "bg-white/10 text-white/85"
                : "text-white/35"
            } cursor-not-allowed`}
          >
            {tab}
          </button>
        ))}
        <span className="ml-auto text-[11px] text-white/40">
          {total} actionable today across legacy sources
        </span>
      </div>

      <ul className="grid gap-3 sm:grid-cols-2">
        {types.map((t) => (
          <li key={t.key}>
            <article className="flex h-full flex-col gap-3 rounded-xl border border-white/10 bg-white/[0.02] p-4">
              <header className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="font-display text-base font-bold text-white">
                    {t.label}
                  </h3>
                  <p className="mt-1 text-[11px] leading-snug text-white/55">
                    {t.blurb}
                  </p>
                </div>
                {t.status === "live-source" ? (
                  <span className="shrink-0 rounded-full border border-aqua/30 bg-aqua/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-aqua/90">
                    {t.count ?? 0}
                  </span>
                ) : (
                  <span className="shrink-0 rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] font-bold uppercase tracking-widest text-white/45">
                    Phase 2
                  </span>
                )}
              </header>
              {t.status === "live-source" && t.sourceHref ? (
                <Link
                  href={t.sourceHref}
                  className="mt-auto inline-flex items-center gap-1 text-[12px] font-semibold text-aqua hover:underline"
                >
                  Open legacy view →
                </Link>
              ) : (
                <span className="mt-auto text-[11px] italic text-white/35">
                  No source table yet — added by P2-08 intake hooks.
                </span>
              )}
            </article>
          </li>
        ))}
      </ul>

      <Card className="mt-6">
        <CardHeader
          title="What ships in Phase 2"
          subtitle="See documentation/internal/inbox-redesign-planning.md §6 for the ticket breakdown."
        />
        <ul className="mt-2 space-y-1 text-[12px] leading-snug text-white/65">
          <li>
            • Single ranked list (oldest first), filterable by{" "}
            <code className="text-aqua/90">Mine / All / Unassigned</code>,
            type, status, repo.
          </li>
          <li>
            • Auto-assignment via routing rules — Plays declare abstract
            handles (<code className="text-aqua/90">code_owner</code>,{" "}
            <code className="text-aqua/90">pr_author</code>,{" "}
            <code className="text-aqua/90">release_manager</code>),
            workspace settings map them to users / groups / strategies.
          </li>
          <li>
            • Typed dispositions per item: Approve / Answer / Accept /
            Retry / Acknowledge — each writes back to the source table
            and emits an audit event.
          </li>
          <li>
            • Snooze, reassign, stale badge (2d yellow / 7d red) — full
            state machine in RFC-0010 §Inbox.
          </li>
        </ul>
      </Card>
    </AppShell>
  );
}

function initialsOf(name: string): string {
  return (
    name
      .replace(/[^a-zA-Z0-9 ]/g, " ")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase() ?? "")
      .join("") || "?"
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
    <AppShell title="Inbox">
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
