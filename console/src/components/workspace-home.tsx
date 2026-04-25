import Link from "next/link";

import type {
  ApiOpsBlocker,
  ApiOpsDashboard,
  ApiOpsImpact,
  ApiOpsStatus,
  ApiOpsWorkItem,
} from "@/lib/api/client";
import { Badge, type BadgeTone, Card, CardHeader } from "@/components/ui";

export type WorkspaceHomeProps = {
  summary: ApiOpsDashboard;
};

export function WorkspaceHome({ summary }: WorkspaceHomeProps) {
  return (
    <>
      <section className="grid grid-cols-1 gap-4 lg:grid-cols-[1.4fr_1fr]">
        <SystemStatusCard summary={summary} />
        <SuggestedActionsCard summary={summary} />
      </section>

      <section className="mt-4 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <BlockersCard blockers={summary.blockers.slice(0, 7)} />
        <WorkInProgressCard items={summary.work_in_progress.slice(0, 7)} />
        <ShippedCard summary={summary} />
        <BottlenecksCard summary={summary} />
        <AutomationHealthCard summary={summary} />
      </section>
    </>
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
          <h2 className="mt-3 font-display text-2xl font-bold text-white">
            {title}
          </h2>
          <p className="mt-1 max-w-2xl text-sm leading-relaxed text-white/60">
            The dashboard shows only issues, active work and decisions needed
            now. Historical and setup metrics stay out of this view.
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
      <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <StatusMetric
          label="Failing pipelines"
          value={status.failing_pipelines_count}
          tone={status.failing_pipelines_count > 0 ? "err" : "ok"}
        />
        <StatusMetric
          label="Stuck PRs"
          value={status.stuck_prs_count}
          tone={status.stuck_prs_count > 0 ? "warn" : "ok"}
        />
        <StatusMetric
          label="Broken automations"
          value={status.broken_automations_count}
          tone={status.broken_automations_count > 0 ? "err" : "ok"}
        />
      </div>
    </Card>
  );
}

function SuggestedActionsCard({ summary }: { summary: ApiOpsDashboard }) {
  return (
    <Card>
      <CardHeader
        title="Suggested Actions"
        subtitle="Priority actions generated from current blockers."
      />
      {summary.suggested_actions.length === 0 ? (
        <EmptyText>No suggested actions.</EmptyText>
      ) : (
        <ul className="space-y-3">
          {summary.suggested_actions.slice(0, 5).map((item) => (
            <li key={`${item.action}-${item.reason}`}>
              <ActionRow
                href={item.href}
                title={item.action}
                meta={item.reason}
                badge={item.priority}
                badgeTone={impactTone(item.priority)}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function BlockersCard({ blockers }: { blockers: ApiOpsBlocker[] }) {
  return (
    <Card>
      <CardHeader
        title="Blockers"
        subtitle="Highest-impact issues, sorted by impact and age."
      />
      {blockers.length === 0 ? (
        <EmptyText>No blockers.</EmptyText>
      ) : (
        <ul className="space-y-3">
          {blockers.map((item) => (
            <li key={`${item.type}-${item.title}-${item.age_seconds}`}>
              <ActionRow
                href={item.href}
                title={item.title}
                meta={[item.repo, item.scope, formatAge(item.age_seconds)]
                  .filter(Boolean)
                  .join(" · ")}
                badge={item.impact}
                badgeTone={impactTone(item.impact)}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function WorkInProgressCard({ items }: { items: ApiOpsWorkItem[] }) {
  return (
    <Card>
      <CardHeader
        title="Work In Progress"
        subtitle="Active work updated in the last 7 days."
      />
      {items.length === 0 ? (
        <EmptyText>No active WIP.</EmptyText>
      ) : (
        <ul className="space-y-3">
          {items.map((item) => (
            <li key={`${item.name}-${item.updated_at}`}>
              <ActionRow
                href={item.href}
                title={item.name}
                meta={[item.repo, item.scope, formatDate(item.updated_at)]
                  .filter(Boolean)
                  .join(" · ")}
                badge={item.status.replace("_", " ")}
                badgeTone={item.status === "blocked" ? "err" : "info"}
              />
            </li>
          ))}
        </ul>
      )}
    </Card>
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

function BottlenecksCard({ summary }: { summary: ApiOpsDashboard }) {
  return (
    <Card>
      <CardHeader
        title="Bottlenecks"
        subtitle="Detected signals, not long-range charts."
      />
      {summary.bottlenecks.length === 0 ? (
        <EmptyText>No bottlenecks detected.</EmptyText>
      ) : (
        <ul className="space-y-3">
          {summary.bottlenecks.slice(0, 5).map((item) => (
            <li
              key={item.metric}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-3"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-sm font-semibold text-white">
                    {item.metric}
                  </div>
                  <div className="mt-1 text-xs text-white/50">
                    Current: {item.current_value}
                    {item.delta ? ` · ${item.delta}` : ""}
                  </div>
                </div>
                <Badge tone={impactTone(item.severity)}>{item.severity}</Badge>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function AutomationHealthCard({ summary }: { summary: ApiOpsDashboard }) {
  const health = summary.automation_health;

  return (
    <Card>
      <CardHeader
        title="Automation Health"
        subtitle="Last 24h execution and intervention signals."
      />
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-2">
        <MiniMetric
          label="Coverage"
          value={
            health.automation_coverage === null
              ? "Not tracked"
              : formatPercent(health.automation_coverage)
          }
        />
        <MiniMetric
          label="Success rate"
          value={
            health.success_rate === null
              ? "No runs"
              : formatPercent(health.success_rate)
          }
          tone={health.success_rate !== null && health.success_rate < 0.8 ? "warn" : "ok"}
        />
        <MiniMetric
          label="Manual asks"
          value={health.manual_interventions_count}
          tone={health.manual_interventions_count > 0 ? "warn" : "ok"}
        />
        <MiniMetric
          label="Failures"
          value={health.failures_count}
          tone={health.failures_count > 0 ? "err" : "ok"}
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

function impactTone(impact: ApiOpsImpact): BadgeTone {
  if (impact === "high") return "err";
  if (impact === "medium") return "warn";
  return "info";
}

function formatAge(seconds: number): string {
  const hours = Math.floor(seconds / 3600);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h old`;
  const days = Math.floor(hours / 24);
  return `${days}d old`;
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
