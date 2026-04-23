/**
 * MIGRATED: /artifact-feedback → /settings/catalog-feedback per RFC-0010 P2-18.
 *
 * Catalog feedback inbox — formerly the workspace-wide
 * "Artifact feedback" surface, now scoped admin-only under
 * Settings (planning §1: ``ArtifactFeedback exits operator inbox
 * → admin-only at /settings/catalog-feedback``).
 *
 * The body is essentially the same as the legacy
 * ``/artifact-feedback`` page — feedback rows are complaints from
 * humans + agents against catalog artifacts (patterns, tools,
 * collections). Triage is unchanged: status transitions + an
 * optional linked PR URL.
 *
 * The admin guard is the only behavioural change. We resolve the
 * caller's membership and render a 401-style card if they aren't
 * an admin or owner. The URL itself is stable (no 404) so admins
 * who bookmarked the new path don't need to refresh stale tabs.
 */
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Card, CardHeader } from "@/components/ui";
import {
  type ApiArtifactFeedback,
  type ApiArtifactFeedbackStatus,
  ApiHttpError,
  ApiUnavailableError,
  getMe,
  isApiConfigured,
  listActivatedRepos,
  listArtifactFeedback,
  listMembers,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { FeedbackRow } from "./feedback-row";

export const dynamic = "force-dynamic";

const STATUSES: readonly ApiArtifactFeedbackStatus[] = [
  "open",
  "triaged",
  "merged",
  "closed",
];

const ADMIN_ROLES = new Set(["owner", "admin"]);

export default async function CatalogFeedbackPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status: rawStatus } = await searchParams;
  const activeStatus = STATUSES.includes(
    rawStatus as ApiArtifactFeedbackStatus,
  )
    ? (rawStatus as ApiArtifactFeedbackStatus)
    : undefined;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Catalog feedback" kicker="ADMIN">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to view catalog feedback."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fsettings%2Fcatalog-feedback");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fsettings%2Fcatalog-feedback");
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  // Admin guard. We pull the membership list + ``getMe`` instead of
  // a dedicated ``/me/role`` endpoint because the API doesn't ship
  // one yet — listMembers is admin-readable for everyone in the
  // workspace, so the call itself is safe. ``getMe`` failing means
  // we couldn't identify the caller; treat that as not-admin.
  const me = await getMe(token).catch(() => null);
  let isAdmin = false;
  if (me) {
    try {
      const members = await listMembers(workspace.id, token);
      const myMembership = members.find((m) => m.user_id === me.id);
      isAdmin = myMembership ? ADMIN_ROLES.has(myMembership.role) : false;
    } catch {
      // listMembers can 403 for low-priv members; treat as not-admin.
      isAdmin = false;
    }
  }

  if (!isAdmin) {
    return (
      <AppShell
        title="Catalog feedback"
        kicker="ADMIN"
        workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      >
        <Card>
          <CardHeader
            title="401 — admins only"
            subtitle="Catalog feedback triage is restricted to workspace admins and owners. Ask one of them to share a summary or change your role."
          />
        </Card>
      </AppShell>
    );
  }

  let items: ApiArtifactFeedback[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  try {
    [items, repos] = await Promise.all([
      listArtifactFeedback(workspace.id, { status: activeStatus, token }),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fsettings%2Fcatalog-feedback");
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Catalog feedback"
      kicker="ADMIN"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repos[0]?.id ?? null,
      }}
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Complaints + approvals against catalog artifacts — patterns,
        tools, collections — filed by agents and humans across the
        workspace. Triage is admin-only because the catalog itself is
        a Ship-curated surface.
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        <FilterPill
          label="all"
          href="/settings/catalog-feedback"
          active={!activeStatus}
        />
        {STATUSES.map((s) => (
          <FilterPill
            key={s}
            label={s}
            href={`/settings/catalog-feedback?status=${s}`}
            active={activeStatus === s}
          />
        ))}
      </div>

      {items.length === 0 ? (
        <Card>
          <CardHeader
            title="No feedback yet"
            subtitle="Feedback shows up here when the agent (or the team) files a complaint against a catalog artifact."
          />
        </Card>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <FeedbackRow
              key={item.id}
              workspaceId={workspace.id}
              item={item}
            />
          ))}
        </ul>
      )}
    </AppShell>
  );
}

function FilterPill({
  label,
  href,
  active,
}: {
  label: string;
  href: string;
  active: boolean;
}) {
  // Plain anchor (vs ``next/link``) keeps the page server-only —
  // no client bundle for what is functionally a sticky URL pill.
  return (
    <a
      href={href}
      className={`rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition ${
        active
          ? "border-aqua/40 bg-aqua/10 text-aqua"
          : "border-white/10 bg-white/[0.02] text-white/60 hover:border-white/20 hover:text-white"
      }`}
    >
      {label}
    </a>
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
    <AppShell title="Catalog feedback" kicker="ADMIN">
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
