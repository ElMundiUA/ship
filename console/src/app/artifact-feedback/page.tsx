import Link from "next/link";
import { redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiArtifactFeedback,
  type ApiArtifactFeedbackStatus,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listActivatedRepos,
  listArtifactFeedback,
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

import { FeedbackRow } from "./feedback-row";

/**
 * Artifact feedback inbox (C12).
 *
 * This is the counterpart to the improvements page: improvements
 * are proposed changes to the tenant's repo, feedback is the
 * user's complaints against *our* catalog (patterns, tools,
 * collections). The agent can file feedback too via the
 * ``create_artifact_feedback`` tool, so every row here has both
 * human and agent contributors.
 *
 * Triage flow is minimal on purpose: status transitions and an
 * optional linked PR URL. The full "merge this into the catalog"
 * loop happens out-of-band (editing the catalog YAML lives in
 * a separate repo, not in the console).
 */

export const dynamic = "force-dynamic";

const STATUSES: readonly ApiArtifactFeedbackStatus[] = [
  "open",
  "triaged",
  "merged",
  "closed",
];

export default async function ArtifactFeedbackPage({
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
      <AppShell title="Artifact feedback">
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to view feedback."
          />
        </Card>
      </AppShell>
    );
  }

  const token = await getSessionToken();
  if (!token) redirect("/login?next=%2Fartifact-feedback");

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fartifact-feedback");
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let items: ApiArtifactFeedback[] = [];
  let repos: Awaited<ReturnType<typeof listActivatedRepos>> = [];
  try {
    [items, repos] = await Promise.all([
      listArtifactFeedback(workspace.id, { status: activeStatus, token }),
      listActivatedRepos(workspace.id, token).catch(() => []),
    ]);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401)
      redirect("/login?next=%2Fartifact-feedback");
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title="Artifact feedback"
      workspace={{ id: workspace.id, name: workspace.name, slug: workspace.slug }}
      scope={{
        repos: repos.map((r) => ({ id: r.id, full_name: r.full_name })),
        selectedRepoId: repos[0]?.id ?? null,
      }}
      actions={
        <Link
          href="/improvements"
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          Improvements →
        </Link>
      }
    >
      <p className="mb-4 max-w-2xl text-xs text-white/55">
        Complaints against catalog artifacts — patterns, tools,
        collections. The agent files these automatically when a user reports
        that a recipe was wrong; humans can file them from any
        artifact page.
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        <FilterPill
          label="all"
          href="/artifact-feedback"
          active={!activeStatus}
        />
        {STATUSES.map((s) => (
          <FilterPill
            key={s}
            label={s}
            href={`/artifact-feedback?status=${s}`}
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
  return (
    <Link
      href={href}
      className={`rounded-full border px-3 py-1 text-[11px] font-semibold uppercase tracking-wider transition ${
        active
          ? "border-aqua/40 bg-aqua/10 text-aqua"
          : "border-white/10 bg-white/[0.02] text-white/60 hover:border-white/20 hover:text-white"
      }`}
    >
      {label}
    </Link>
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
    <AppShell title="Artifact feedback">
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
