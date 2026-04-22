"use client";

import Link from "next/link";
import { useMemo } from "react";

import { Card, CardHeader } from "@/components/ui";
import type {
  ApiActivatedRepo,
  ApiLane,
  ApiLaneCatalogEntry,
} from "@/lib/api/client";

import { WeeklyCalendar } from "./weekly-calendar";

/**
 * Active tab — calendar-first projection of the workspace lanes.
 *
 * Wraps {@link WeeklyCalendar} with the contextual chrome that
 * doesn't belong inside the calendar itself:
 *
 * - Empty state + "Sync now" CTAs per repo (so an operator who just
 *   merged a config can poke Ship without leaving the page).
 * - The "Edit schedule" handler that deep-links into
 *   ``/lanes?tab=library&open=<kind>`` — the Library card recognises
 *   the query param and opens its wizard pre-populated.
 * - Sync-status footer with the last sync timestamp per repo so the
 *   user can tell whether the calendar is stale.
 */

export function ActiveCalendar({
  workspaceId,
  lanes,
  repos,
  catalog,
}: {
  workspaceId: string;
  lanes: ApiLane[];
  repos: ApiActivatedRepo[];
  catalog: ApiLaneCatalogEntry[];
}) {
  const hasAnyLane = lanes.length > 0;

  const lastSyncByRepo = useMemo(() => {
    const m = new Map<string, string>();
    for (const l of lanes) {
      if (!l.synced_at) continue;
      const cur = m.get(l.repo_id);
      if (!cur || l.synced_at > cur) m.set(l.repo_id, l.synced_at);
    }
    return m;
  }, [lanes]);

  function handleEdit(lane: ApiLane) {
    // The Library card for the built-in recipe reads ``?open=<kind>``
    // and expands itself with the wizard seeded from this lane's
    // cron. For custom (config-only) lanes we still land in Library
    // so the user can at least see them in context.
    window.location.href = `/lanes?tab=library&open=${encodeURIComponent(
      lane.lane_id,
    )}&repo_id=${encodeURIComponent(lane.repo_id)}`;
  }

  if (!hasAnyLane) {
    return <EmptyActive workspaceId={workspaceId} repos={repos} />;
  }

  return (
    <div className="space-y-5">
      <WeeklyCalendar lanes={lanes} catalog={catalog} onEdit={handleEdit} />

      <RepoSyncFooter
        workspaceId={workspaceId}
        repos={repos}
        lastSync={lastSyncByRepo}
      />
    </div>
  );
}

function EmptyActive({
  workspaceId,
  repos,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
}) {
  return (
    <Card>
      <CardHeader
        title="No lanes on the calendar yet"
        subtitle="Lanes show up here the moment .ship/config.yml lands on a repo's default branch."
      />
      <div className="mt-4 text-xs text-white/60">
        <p>Quick start:</p>
        <ol className="mt-1 ml-5 list-decimal space-y-1">
          <li>
            Open the{" "}
            <Link
              href="/lanes?tab=library"
              className="text-aqua hover:underline"
            >
              Library
            </Link>{" "}
            tab and add a recipe — it opens a PR against{" "}
            <code className="rounded bg-white/[0.06] px-1 py-0.5">
              .ship/config.yml
            </code>
            .
          </li>
          <li>
            Merge the PR and push to the default branch — the webhook
            auto-syncs the calendar.
          </li>
          <li>Or hit &ldquo;Sync now&rdquo; on a repo below.</li>
        </ol>
      </div>
      {repos.length > 0 ? (
        <div className="mt-4 space-y-2">
          {repos.map((repo) => (
            <SyncButton
              key={repo.id}
              workspaceId={workspaceId}
              repoId={repo.id}
              label={`Sync ${repo.full_name}`}
            />
          ))}
        </div>
      ) : null}
    </Card>
  );
}

function RepoSyncFooter({
  workspaceId,
  repos,
  lastSync,
}: {
  workspaceId: string;
  repos: ApiActivatedRepo[];
  lastSync: Map<string, string>;
}) {
  if (repos.length === 0) return null;
  return (
    <div className="rounded-md border border-white/10 bg-white/[0.02] p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="font-display text-xs font-semibold uppercase tracking-wide text-white/55">
          Sync status
        </h4>
        <span className="text-[10px] text-white/35">
          calendar refreshes on push to default branch
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {repos.map((repo) => {
          const ts = lastSync.get(repo.id);
          return (
            <div
              key={repo.id}
              className="flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1"
            >
              <code className="font-mono text-[10px] text-white/70">
                {repo.full_name}
              </code>
              <span className="text-[10px] text-white/45">
                {ts ? `synced ${formatRelative(ts)}` : "never synced"}
              </span>
              <SyncButton
                workspaceId={workspaceId}
                repoId={repo.id}
                label="sync"
                compact
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SyncButton({
  workspaceId,
  repoId,
  label,
  compact,
}: {
  workspaceId: string;
  repoId: string;
  label: string;
  compact?: boolean;
}) {
  return (
    <form action="/api/dashboard/sync-lanes" method="POST">
      <input type="hidden" name="ws" value={workspaceId} />
      <input type="hidden" name="repo" value={repoId} />
      <button
        type="submit"
        className={
          compact
            ? "rounded border border-white/15 bg-white/[0.05] px-1.5 py-0.5 text-[10px] font-semibold text-white/70 hover:text-white"
            : "rounded border border-white/15 bg-white/[0.05] px-3 py-1 text-[11px] font-semibold text-white/85 hover:border-white/30 hover:text-white"
        }
      >
        {label}
      </button>
    </form>
  );
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
