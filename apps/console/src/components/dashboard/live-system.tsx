import type { ApiLiveSystem } from "@/lib/api/client";
import { cn } from "@/lib/cn";

/**
 * Dashboard v2 Live System block (PR-2).
 *
 * Quiet middle column on the home dashboard. Reads as system health
 * at a glance — kicker masthead + four cells (Knowledge, Routines,
 * Daily, Specialists). No bordered cards; section breaks come from
 * whitespace + tinted kickers.
 *
 * Fallback shape: if the aggregator endpoint failed to load the
 * caller passes ``null`` and we render the loading placeholder so
 * the column doesn't read broken when one upstream blip happens.
 */

type Props = {
  data: ApiLiveSystem | null;
};

export function DashboardLiveSystem({ data }: Props) {
  if (data === null) {
    return (
      <section className="space-y-2">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
          Live system · loading…
        </p>
        <p className="text-[11px] italic text-white/30">
          Couldn&apos;t reach the live-system aggregator on this render — try a
          refresh.
        </p>
      </section>
    );
  }

  return (
    <section className="space-y-6">
      <Masthead data={data.masthead} />
      <KnowledgeCell data={data.knowledge} />
      <RoutinesCell rows={data.routines} />
      <DailyCell data={data.daily} />
      <SpecialistsCell data={data.specialists} />
    </section>
  );
}


function Masthead({ data }: { data: ApiLiveSystem["masthead"] }) {
  const successPct =
    data.success_rate_7d !== null
      ? `${Math.round(data.success_rate_7d * 100)}%`
      : "—";
  const last =
    data.last_run_at !== null
      ? `${formatRelative(data.last_run_at)} last run`
      : "no runs yet";
  const failuresChip =
    data.failures_7d > 0 ? (
      <>
        <span className="mx-2 text-white/15">·</span>
        <span className="text-coral">
          {data.failures_7d} failure{data.failures_7d === 1 ? "" : "s"}
        </span>
      </>
    ) : null;
  return (
    <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
      Live system
      <span className="mx-2 text-white/15">·</span>
      <span className={data.last_run_status === "error" ? "text-coral" : "text-aqua/80"}>
        {successPct}
      </span>
      <span className="mx-2 text-white/15">·</span>
      <span>{last}</span>
      {failuresChip}
    </p>
  );
}


function KnowledgeCell({ data }: { data: ApiLiveSystem["knowledge"] }) {
  const stateColor =
    data.state_label === "errored"
      ? "text-coral"
      : data.state_label === "running"
        ? "text-aqua"
        : "text-white/65";
  return (
    <div className="space-y-1">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Knowledge
      </p>
      <p className="text-[12px]">
        <span className={cn("font-semibold", stateColor)}>
          {data.state_label}
        </span>
        <span className="mx-2 text-white/20">·</span>
        <span className="text-white/65">
          {data.ingested_today} ingested today
        </span>
      </p>
    </div>
  );
}


function RoutinesCell({ rows }: { rows: ApiLiveSystem["routines"] }) {
  return (
    <div className="space-y-1.5">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Routines
      </p>
      {rows.length === 0 ? (
        <p className="text-[11px] italic text-white/30">No active routines.</p>
      ) : (
        <ul className="space-y-1">
          {rows.slice(0, 2).map((row) => (
            <li
              key={row.name}
              className="flex items-baseline justify-between gap-3 text-[12px]"
            >
              <span className="truncate text-white/80">{row.name}</span>
              <span className="shrink-0 text-[11px]">
                <span
                  className={cn(
                    row.last_run_status === "failed" ||
                      row.last_run_status === "error"
                      ? "text-coral"
                      : "text-white/45",
                  )}
                >
                  {row.last_run_at
                    ? `last ${formatRelative(row.last_run_at)}`
                    : "never run"}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}


function DailyCell({ data }: { data: ApiLiveSystem["daily"] }) {
  const last =
    data.last_sent_at !== null
      ? `sent ${formatRelative(data.last_sent_at)}`
      : data.last_run_status === "failed" || data.last_run_status === "error"
        ? "skipped"
        : "not yet sent";
  return (
    <div className="space-y-1.5">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Daily
      </p>
      <ul className="space-y-1 text-[12px]">
        <li className="flex items-baseline gap-2">
          <span className="text-white/45">Last</span>
          <span className="text-white/20">·</span>
          <span
            className={cn(
              data.last_run_status === "failed" ||
                data.last_run_status === "error"
                ? "text-coral"
                : "text-white/80",
            )}
          >
            {last}
          </span>
        </li>
        <li className="flex items-baseline gap-2">
          <span className="text-white/45">Queue</span>
          <span className="text-white/20">·</span>
          <span className="text-white/80">
            {data.queue_wins === 0 && data.queue_blockers === 0
              ? "empty"
              : (
                <>
                  <span className="text-aqua/85">
                    {data.queue_wins} win{data.queue_wins === 1 ? "" : "s"}
                  </span>
                  <span className="mx-1 text-white/20">·</span>
                  <span
                    className={
                      data.queue_blockers > 0 ? "text-coral" : "text-white/45"
                    }
                  >
                    {data.queue_blockers} blocker
                    {data.queue_blockers === 1 ? "" : "s"}
                  </span>
                </>
              )}
          </span>
        </li>
      </ul>
    </div>
  );
}


function SpecialistsCell({
  data,
}: {
  data: ApiLiveSystem["specialists"];
}) {
  const total = data.idle_count + data.working_count + data.errored_count;
  if (total === 0) {
    return (
      <div className="space-y-1">
        <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
          Specialists
        </p>
        <p className="text-[11px] italic text-white/30">
          None configured yet.
        </p>
      </div>
    );
  }

  const parts: React.ReactNode[] = [];
  if (data.errored_count > 0) {
    parts.push(
      <span key="err" className="text-coral">
        {data.errored_count === 1 && data.errored_names.length === 1
          ? `${data.errored_names[0]} errored`
          : `${data.errored_count} errored`}
      </span>,
    );
  }
  if (data.working_count > 0) {
    parts.push(
      <span key="work" className="text-aqua">
        {data.working_name
          ? `${data.working_name} working`
          : `${data.working_count} working`}
      </span>,
    );
  }
  if (data.idle_count > 0) {
    parts.push(
      <span key="idle" className="text-white/65">
        {data.idle_count} idle
      </span>,
    );
  }

  return (
    <div className="space-y-1">
      <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-white/40">
        Specialists
      </p>
      <p className="text-[12px]">
        {parts.flatMap((node, idx) =>
          idx === 0
            ? [node]
            : [
                <span key={`sep-${idx}`} className="mx-2 text-white/20">
                  ·
                </span>,
                node,
              ],
        )}
      </p>
      {data.errored_count > 1 && data.errored_names.length > 0 && (
        <p className="text-[10.5px] text-coral/80">
          {data.errored_names.join(", ")}
        </p>
      )}
    </div>
  );
}


function formatRelative(value: string): string {
  const date = Date.parse(value);
  if (Number.isNaN(date)) return "—";
  const diff = Date.now() - date;
  const minute = 60_000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (diff < minute) return "just now";
  if (diff < hour) return `${Math.max(1, Math.round(diff / minute))}m ago`;
  if (diff < day) return `${Math.round(diff / hour)}h ago`;
  if (diff < 30 * day) return `${Math.round(diff / day)}d ago`;
  return new Date(date).toLocaleDateString();
}
