import Link from "next/link";

import type {
  ApiActivatedRepo,
  ApiOpsDashboard,
  ApiOpsStatus,
  ApiOpsWorkItem,
} from "@/lib/api/client";
import { Badge, type BadgeTone, Card, CardHeader } from "@/components/ui";

export type WorkspaceHomeProps = {
  summary: ApiOpsDashboard;
  repos: ApiActivatedRepo[];
  workspaceId: string;
};

export function WorkspaceHome({ summary, repos, workspaceId }: WorkspaceHomeProps) {
  const reposNeedingShipUpdate = repos.filter(needsShipTemplateUpdate);

  return (
    <>
      {reposNeedingShipUpdate.length > 0 && (
        <NeedsShipUpdateBanner repos={reposNeedingShipUpdate} workspaceId={workspaceId} />
      )}

      <section className="grid grid-cols-1 gap-4">
        <SystemStatusCard summary={summary} />
      </section>

      <section className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <WorkInProgressCard items={summary.work_in_progress.slice(0, 7)} />
        <ShippedCard summary={summary} />
        <AutomationTestingCard summary={summary} />
      </section>
    </>
  );
}

function needsShipTemplateUpdate(repo: ApiActivatedRepo): boolean {
  const installed = repo.installed_bundle_version;
  const current = repo.current_bundle_version;
  if (installed == null) return true;
  return installed < current;
}

function NeedsShipUpdateBanner({
  repos,
  workspaceId,
}: {
  repos: ApiActivatedRepo[];
  workspaceId: string;
}) {
  const configureHref = `/onboarding?step=configure&ws=${encodeURIComponent(workspaceId)}`;
  return (
    <section className="mb-4 rounded-2xl border border-aqua/25 bg-aqua/[0.06] px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-display text-sm font-bold text-white">Ship template update needed</h3>
          <p className="mt-1 text-xs text-white/60">
            These repos are behind the current Ship bundle — open the wizard to refresh workflows and
            config.
          </p>
        </div>
        <Link
          href={configureHref}
          className="shrink-0 rounded-full border border-aqua/50 bg-aqua/10 px-3 py-1.5 text-xs font-bold text-aqua hover:bg-aqua/20"
        >
          Open wizard →
        </Link>
      </div>
      <ul className="mt-3 space-y-2">
        {repos.map((repo) => (
          <li
            key={repo.id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-white/10 bg-white/[0.04] px-3 py-2 text-xs"
          >
            <span className="font-mono text-white/90">{repo.full_name}</span>
            <span className="text-white/50">
              {repo.installed_bundle_version == null
                ? "Not seeded yet"
                : `v${repo.installed_bundle_version} → v${repo.current_bundle_version} available`}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function SystemStatusCard({ summary }: { summary: ApiOpsDashboard }) {
  const status = summary.system_status;
  const tone = statusTone(status.overall_status);
  const title =
    status.overall_status === "critical"
      ? "Critical issues need attention"
      : status.overall_status === "degraded"
        ? "Workspace is degraded"
        : "Workspace is healthy";

  return (
    <Card className="relative overflow-hidden">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <Badge tone={tone} dot>
            {status.overall_status}
          </Badge>
          <h2 className="mt-3 font-display text-2xl font-bold text-white">{title}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-white/60">
            Blockers and idle work surface in{" "}
            <Link href="/inbox" className="text-aqua hover:underline">
              Inbox
            </Link>
            . This card tracks failing pipelines and automation signals only.
          </p>
        </div>
        <div className="text-right text-xs text-white/45">
          <div>Last deploy</div>
          <div className="mt-1 font-semibold text-white/75">
            {status.last_deploy?.time
              ? `${formatDate(status.last_deploy.time)} · ${status.last_deploy.status ?? "unknown"}`
              : "Not tracked yet"}
          </div>
        </div>
      </div>
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <StatusMetric
          label="Failed pipeline runs (24h)"
          value={status.failing_pipelines_count}
          tone={status.failing_pipelines_count > 0 ? "err" : "ok"}
        />
        <StatusMetric
          label="Broken automations (24h)"
          value={status.broken_automations_count}
          tone={status.broken_automations_count > 0 ? "err" : "ok"}
        />
      </div>
    </Card>
  );
}

function WorkInProgressCard({ items }: { items: ApiOpsWorkItem[] }) {
  return (
    <Card>
      <CardHeader
        title="Work in progress"
        subtitle="Open tickets from each repo’s connected tracker (GitHub Issues / Linear / Jira). Without a tracker binding, open PRs fill this list instead."
      />
      {items.length === 0 ? (
        <EmptyText>No active WIP.</EmptyText>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={`${item.name}-${item.updated_at}`}>
              <WorkInProgressRow item={item} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function WorkInProgressRow({ item }: { item: ApiOpsWorkItem }) {
  const pr = item.pull_request;
  const href = item.href;
  const hrefIsExternal =
    href != null && (href.startsWith("http://") || href.startsWith("https://"));
  const chips = [
    item.board_column,
    item.tracker ? `Tracker · ${item.tracker}` : null,
    item.active_agent ? `Agent · ${item.active_agent}` : null,
    item.repo,
    formatDate(item.updated_at),
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="group flex flex-col gap-2 rounded-xl border border-white/10 bg-white/[0.03] p-3 transition hover:border-white/25 hover:bg-white/[0.06]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          {href ? (
            hrefIsExternal ? (
              <a href={href} className="block" target="_blank" rel="noreferrer">
                <div className="truncate text-sm font-semibold text-white group-hover:text-aqua">
                  {item.name}
                </div>
              </a>
            ) : (
              <Link href={href} className="block">
                <div className="truncate text-sm font-semibold text-white group-hover:text-aqua">
                  {item.name}
                </div>
              </Link>
            )
          ) : (
            <div className="truncate text-sm font-semibold text-white">{item.name}</div>
          )}
          <div className="mt-1 truncate text-xs text-white/50">{chips}</div>
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          {item.ticket_ref ? (
            <Badge tone="ok">{item.ticket_ref}</Badge>
          ) : (
            <Badge tone="neutral">No ticket key in title</Badge>
          )}
          <Badge tone={item.status === "blocked" ? "err" : "info"}>
            {item.status.replace("_", " ")}
          </Badge>
        </div>
      </div>
      {pr ? (
        <div className="flex flex-wrap gap-2">
          <a
            href={pr.href}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center rounded-lg border border-white/15 bg-white/[0.05] px-2.5 py-1 text-[11px] font-semibold text-white/75 hover:border-aqua/40 hover:text-aqua"
          >
            PR #{pr.number} →
          </a>
        </div>
      ) : null}
    </div>
  );
}

function ShippedCard({ summary }: { summary: ApiOpsDashboard }) {
  const shipped = summary.shipped;

  return (
    <Card>
      <CardHeader title="Shipped Last 24h" subtitle="Merged work by outcome." />
      <div className="grid grid-cols-3 gap-2">
        <MiniMetric label="Features" value={shipped.features_shipped_count} />
        <MiniMetric label="Fixes" value={shipped.fixes_count} />
        <MiniMetric label="Rollbacks" value={shipped.rollbacks_count} tone="warn" />
      </div>
      <div className="mt-4">
        {shipped.items.length === 0 ? (
          <EmptyText>No shipped items in the last 24h.</EmptyText>
        ) : (
          <ul className="space-y-3">
            {shipped.items.slice(0, 3).map((item) => (
              <li key={`${item.type}-${item.name}`}>
                <ActionRow
                  href={item.href}
                  title={item.name}
                  meta={item.repo ?? "Workspace"}
                  badge={item.type}
                  badgeTone={item.type === "rollback" ? "warn" : "ok"}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

function AutomationTestingCard({ summary }: { summary: ApiOpsDashboard }) {
  const health = summary.automation_health;

  return (
    <Card>
      <CardHeader
        title="Automation testing (24h)"
        subtitle="Pipeline run success rate and related CI failures — not overall process health."
      />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <MiniMetric
          label="Success rate"
          value={
            health.success_rate === null ? "No executions" : formatPercent(health.success_rate)
          }
          tone={health.success_rate !== null && health.success_rate < 0.8 ? "warn" : "ok"}
        />
        <MiniMetric
          label="Failures"
          value={health.failures_count}
          tone={health.failures_count > 0 ? "err" : "ok"}
        />
        <MiniMetric
          label="Manual asks"
          value={health.manual_interventions_count}
          tone={health.manual_interventions_count > 0 ? "warn" : "ok"}
        />
        <MiniMetric
          label="Coverage"
          value={
            health.automation_coverage === null
              ? "Not tracked"
              : formatPercent(health.automation_coverage)
          }
        />
      </div>
    </Card>
  );
}

function StatusMetric({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: BadgeTone;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/40">
        {label}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className="font-display text-3xl font-bold text-white">{value}</span>
        <Badge tone={tone}>{value > 0 ? "attention" : "clear"}</Badge>
      </div>
    </div>
  );
}

function MiniMetric({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number | string;
  tone?: BadgeTone;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
      <div className="text-[10px] font-bold uppercase tracking-[0.18em] text-white/40">
        {label}
      </div>
      <div className="mt-2 text-lg font-bold text-white">{value}</div>
      <div className="mt-2">
        <Badge tone={tone}>24h</Badge>
      </div>
    </div>
  );
}

function ActionRow({
  href,
  title,
  meta,
  badge,
  badgeTone,
}: {
  href: string | null;
  title: string;
  meta: string;
  badge: string;
  badgeTone: BadgeTone;
}) {
  const content = (
    <div className="group flex items-start justify-between gap-3 rounded-xl border border-white/10 bg-white/[0.03] p-3 transition hover:border-white/25 hover:bg-white/[0.06]">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-white">{title}</div>
        <div className="mt-1 truncate text-xs text-white/50">{meta}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge tone={badgeTone}>{badge}</Badge>
        {href && <span className="text-white/30 transition group-hover:text-white">→</span>}
      </div>
    </div>
  );

  return href ? <Link href={href}>{content}</Link> : content;
}

function EmptyText({ children }: { children: string }) {
  return (
    <div className="rounded-xl border border-dashed border-white/10 bg-white/[0.02] p-4 text-sm text-white/50">
      {children}
    </div>
  );
}

function statusTone(status: ApiOpsStatus): BadgeTone {
  if (status === "critical") return "err";
  if (status === "degraded") return "warn";
  return "ok";
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}
