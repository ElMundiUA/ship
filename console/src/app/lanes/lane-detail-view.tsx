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
  listWorkspaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

/**
 * Reusable Lane / Automation detail surface.
 *
 * Lives outside ``page.tsx`` because Next.js page files can't
 * export named helpers (the page-validator rejects anything except
 * the standard set). Both ``/lanes/[laneRowId]/page.tsx`` (legacy)
 * and ``/automations/[id]/page.tsx`` (new IA, P1-01) import this
 * view; only ``basePath`` / ``backLabel`` / ``kicker`` differ.
 */

export async function LaneDetailView({
  laneRowId,
  basePath,
  backLabel,
  kicker,
}: {
  laneRowId: string;
  basePath: string;
  backLabel: string;
  kicker: (detail: ApiLaneDetail) => string;
}) {
  const loginNext = encodeURIComponent(`${basePath}/${laneRowId}`);

  if (!isApiConfigured()) {
    return (
      <AppShell title="Lane">
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
  if (!token) redirect(`/login?next=${loginNext}`);

  let workspaces: Awaited<ReturnType<typeof listWorkspaces>>;
  try {
    workspaces = await listWorkspaces(token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      redirect(`/login?next=${loginNext}`);
    }
    return renderUnavailable(err);
  }
  if (workspaces.length === 0) redirect("/onboarding?step=github");
  const workspace = workspaces[0];

  let detail: ApiLaneDetail;
  try {
    detail = await getLane(workspace.id, laneRowId, token);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      if (err.status === 401) redirect(`/login?next=${loginNext}`);
      if (err.status === 404) notFound();
    }
    return renderUnavailable(err);
  }

  return (
    <AppShell
      title={detail.lane_id}
      kicker={kicker(detail)}
      workspace={{
        id: workspace.id,
        name: workspace.name,
        slug: workspace.slug,
      }}
      actions={
        <Link
          href={basePath}
          className="text-xs font-semibold text-white/65 hover:text-white"
        >
          {backLabel}
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

function renderUnavailable(err: unknown) {
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
