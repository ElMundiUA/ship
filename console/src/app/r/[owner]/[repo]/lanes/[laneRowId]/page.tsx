import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Badge, Card, CardHeader } from "@/components/ui";
import {
  type ApiLaneDetail,
  ApiHttpError,
  ApiUnavailableError,
  getLane,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";
import { resolveRepoContext } from "@/lib/repo-context";
import { slugFromParams, type RepoRouteParams } from "@/lib/repo-slug";

/**
 * Repo-scoped lane detail (``/r/<owner>/<repo>/lanes/<laneRowId>``).
 *
 * Same body as the workspace-level detail, but the lane's
 * ``repo_id`` is validated against the resolved ``ctx.repo`` — if
 * someone pastes a lane URL from a different repo into this repo's
 * path we 404 instead of rendering foreign data.
 */

export const dynamic = "force-dynamic";

export default async function RepoLaneDetailPage({
  params,
}: {
  params: Promise<RepoRouteParams & { laneRowId: string }>;
}) {
  const resolved = await params;
  const slug = slugFromParams(resolved);
  if (!slug) notFound();
  const laneRowId = resolved.laneRowId;
  const here = `/r/${slug}/lanes/${encodeURIComponent(laneRowId)}`;

  if (!isApiConfigured()) {
    return (
      <AppShell title="Lane" kicker={`${slug} · repo`}>
        <Card>
          <CardHeader
            title="Backend not configured"
            subtitle="Set SHIP_API_URL to inspect lane details."
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

  let detail: ApiLaneDetail;
  try {
    detail = await getLane(ctx.workspace.id, laneRowId, token);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      if (err.status === 401) redirect(`/login?next=${encodeURIComponent(here)}`);
      if (err.status === 404) notFound();
    }
    return renderUnavailable(err);
  }

  if (detail.repo_id !== ctx.repo.id) notFound();

  return (
    <AppShell
      title={detail.lane_id}
      kicker={`${detail.repo_full_name} · lane`}
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
          href={`/r/${ctx.repo.full_name}/lanes`}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          ← All lanes
        </Link>
      }
    >
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <Card>
          <CardHeader title="Metadata" />
          <dl className="mt-3 space-y-2 text-xs">
            <Row label="Kind">
              <Badge tone="info">{detail.kind}</Badge>
            </Row>
            <Row label="Pattern">
              {detail.pattern ? (
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  {detail.pattern}
                </code>
              ) : (
                <span className="text-white/45">(none)</span>
              )}
            </Row>
            {detail.cron ? (
              <Row label="Cron">
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  {detail.cron}
                </code>
              </Row>
            ) : null}
            {detail.idempotency_key ? (
              <Row label="Idempotency key">
                <code className="rounded bg-white/[0.06] px-1.5 py-0.5">
                  {detail.idempotency_key}
                </code>
              </Row>
            ) : null}
            <Row label="Enabled">
              <Badge tone={detail.enabled ? "ok" : "neutral"}>
                {detail.enabled ? "yes" : "no"}
              </Badge>
            </Row>
            <Row label="Wrapper">
              <code className="rounded bg-white/[0.04] px-1.5 py-0.5">
                .github/workflows/ship-{detail.lane_id}.yml
              </code>
            </Row>
          </dl>
        </Card>

        <Card>
          <CardHeader title="Sync status" />
          <dl className="mt-3 space-y-2 text-xs">
            <Row label="Synced at">
              <span className="text-white/75">
                {formatRelative(detail.synced_at)}
              </span>
            </Row>
            <Row label="Source">
              {detail.sync_source ? (
                <code className="rounded bg-white/[0.04] px-1.5 py-0.5">
                  {detail.sync_source}
                </code>
              ) : (
                <span className="text-white/45">—</span>
              )}
            </Row>
            <Row label="Last run">
              {detail.last_run_at ? (
                <Badge tone={lastRunTone(detail.last_run_status)} dot>
                  {detail.last_run_status ?? "run"} ·{" "}
                  {formatRelative(detail.last_run_at)}
                </Badge>
              ) : (
                <span className="text-white/45">no runs yet</span>
              )}
            </Row>
          </dl>
        </Card>
      </div>

      <Card className="mt-6" padded={false}>
        <div className="flex items-baseline justify-between border-b border-white/10 px-5 py-3">
          <h3 className="font-display text-sm font-bold tracking-wide text-white">
            Raw config
          </h3>
          <p className="text-[11px] text-white/45">
            from .ship/config.yml lanes.{detail.lane_id}
          </p>
        </div>
        <pre className="overflow-x-auto px-5 py-4 text-[11px] leading-5 text-white/80">
          {JSON.stringify(detail.config, null, 2)}
        </pre>
      </Card>

      <Card className="mt-6" padded={false}>
        <div className="flex items-baseline justify-between border-b border-white/10 px-5 py-3">
          <h3 className="font-display text-sm font-bold tracking-wide text-white">
            Recent runs
          </h3>
          <p className="text-[11px] text-white/45">
            {detail.recent_runs.length === 0
              ? "No runs recorded in Ship yet. Check the GitHub Actions tab for executions."
              : `${detail.recent_runs.length} most recent`}
          </p>
        </div>
        {detail.recent_runs.length === 0 ? (
          <div className="px-5 py-4 text-xs text-white/55">
            Ship only records a run here when it was triggered through
            the platform (future &ldquo;Trigger lane&rdquo; button).
            Webhook-driven runs are visible on the GitHub Actions UI.
          </div>
        ) : (
          <table className="min-w-full text-sm">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Run</th>
                <th className="px-4 py-2 text-left font-semibold">Trigger</th>
                <th className="px-4 py-2 text-left font-semibold">Started</th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-left font-semibold">Summary</th>
              </tr>
            </thead>
            <tbody>
              {detail.recent_runs.map((run) => (
                <tr
                  key={run.id}
                  className="border-t border-white/5 hover:bg-white/[0.02]"
                >
                  <td className="px-4 py-2 align-top font-mono text-[11px] text-white/60">
                    {run.id.slice(0, 8)}
                  </td>
                  <td className="px-4 py-2 align-top text-xs text-white/65">
                    {run.trigger}
                  </td>
                  <td className="px-4 py-2 align-top text-xs text-white/55">
                    {run.started_at ? formatRelative(run.started_at) : "—"}
                  </td>
                  <td className="px-4 py-2 align-top">
                    <Badge tone={lastRunTone(run.status)} dot>
                      {run.status}
                    </Badge>
                  </td>
                  <td className="px-4 py-2 align-top text-xs text-white/75">
                    {run.summary ?? "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </AppShell>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <dt className="text-[11px] uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd className="text-xs text-white/85">{children}</dd>
    </div>
  );
}

function lastRunTone(status: string | null): "ok" | "warn" | "err" | "neutral" {
  if (!status) return "neutral";
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  if (
    status === "cancelled" ||
    status === "timed_out" ||
    status === "action_required"
  )
    return "warn";
  if (status === "running") return "neutral";
  return "neutral";
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

function renderUnavailable(err?: unknown) {
  const isUnavailable = err instanceof ApiUnavailableError;
  return (
    <AppShell title="Lane">
      <Card>
        <CardHeader
          title="Couldn't load lane"
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
