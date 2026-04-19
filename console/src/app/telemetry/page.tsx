import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
} from "@/components/ui";
import {
  integrations,
  relativeTime,
  telemetryEvents,
  telemetrySeries,
  workspaces,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export default function TelemetryPage() {
  const max = Math.max(...telemetrySeries.map((d) => d.events));
  const totalEvents = telemetrySeries.reduce((s, d) => s + d.events, 0);
  const totalSuccess = telemetrySeries.reduce((s, d) => s + d.success, 0);
  const successRate = ((totalSuccess / totalEvents) * 100).toFixed(1);

  return (
    <AppShell
      kicker={`${ws.name} · observe`}
      title="Telemetry"
      actions={
        <>
          <ButtonGhost>Export JSONL</ButtonGhost>
          <ButtonPrimary>+ Add exporter</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <p className="mb-5 max-w-3xl text-sm text-white/65">
        Opt-in events from the CLI, lanes, knowledge ingestion and approvals.
        Show them here, fan them out to OpenTelemetry / S3 / a webhook of your
        choice — or drop the export and keep them private to this workspace.
      </p>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Last 7 days · events vs successful"
            subtitle={`${totalEvents.toLocaleString()} events · ${successRate}% success`}
            action={
              <span className="flex items-center gap-3 text-[11px]">
                <Legend swatch="bg-white/30" label="Events" />
                <Legend swatch="bg-aqua" label="Success" />
              </span>
            }
          />
          <div className="grid grid-cols-7 gap-3 px-1">
            {telemetrySeries.map((d) => {
              const total = (d.events / max) * 100;
              const ok = (d.success / max) * 100;
              return (
                <div key={d.day} className="flex flex-col items-center gap-2">
                  <div className="relative flex h-44 w-full items-end justify-center">
                    <div
                      style={{ height: `${total}%` }}
                      className="absolute bottom-0 w-7 rounded-t bg-white/15"
                    />
                    <div
                      style={{ height: `${ok}%` }}
                      className="absolute bottom-0 w-7 rounded-t bg-gradient-to-t from-aqua/80 via-aqua/60 to-aqua/30"
                    />
                  </div>
                  <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
                    {d.day}
                  </div>
                  <div className="text-[10px] text-white/55">{d.events}</div>
                </div>
              );
            })}
          </div>
        </Card>

        <Card>
          <CardHeader title="Top event kinds" subtitle="Last 24h" />
          <ul className="space-y-3 text-sm">
            <KindRow label="shipctl.pattern.fetch" count={184} pct={62} />
            <KindRow label="workflow.run" count={42} pct={48} />
            <KindRow label="knowledge.embed" count={26} pct={30} />
            <KindRow label="tracker.create" count={11} pct={18} />
            <KindRow label="auth.token.mint" count={3} pct={6} />
          </ul>
        </Card>
      </section>

      <section className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Live event stream"
            subtitle="In-app dashboard · also exported to OTel / S3 / webhook"
          />
          <div className="overflow-hidden rounded-xl border border-white/10">
            <table className="min-w-full text-sm">
              <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/45">
                <tr>
                  <th className="px-3 py-2 text-left font-semibold">Time</th>
                  <th className="px-3 py-2 text-left font-semibold">Kind</th>
                  <th className="px-3 py-2 text-left font-semibold">Actor</th>
                  <th className="px-3 py-2 text-left font-semibold">Object</th>
                  <th className="px-3 py-2 text-left font-semibold">Result</th>
                </tr>
              </thead>
              <tbody>
                {telemetryEvents.map((e) => (
                  <tr key={e.id} className="border-t border-white/5 hover:bg-white/[0.02]">
                    <td className="px-3 py-2.5 align-top text-xs text-white/55">
                      {relativeTime(e.ts)}
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-aqua/95">
                        {e.kind}
                      </code>
                    </td>
                    <td className="px-3 py-2.5 align-top text-xs text-white/75">{e.actor}</td>
                    <td className="px-3 py-2.5 align-top text-xs text-white/65">
                      <code className="font-mono">{e.object}</code>
                    </td>
                    <td className="px-3 py-2.5 align-top">
                      <Badge
                        tone={e.result === "ok" ? "ok" : e.result === "warn" ? "warn" : "err"}
                        dot
                      >
                        {e.result}
                      </Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card>
          <CardHeader title="Exporters" subtitle="Where the events go" />
          <ul className="space-y-2.5">
            {integrations.map((i) => (
              <li
                key={i.id}
                className="flex items-start gap-2 rounded-lg border border-white/10 bg-white/[0.02] p-2.5"
              >
                <div className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-gradient-to-br from-white/10 to-white/[0.02] text-xs font-bold uppercase text-white/85">
                  {i.kind.slice(0, 2)}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-semibold text-white">{i.label}</span>
                    <Badge
                      tone={i.status === "connected" ? "ok" : i.status === "warning" ? "warn" : "neutral"}
                      dot
                    >
                      {i.status}
                    </Badge>
                  </div>
                  <div className="mt-0.5 line-clamp-1 text-[11px] text-white/55">{i.detail}</div>
                </div>
              </li>
            ))}
          </ul>
        </Card>
      </section>
    </AppShell>
  );
}

function Legend({ swatch, label }: { swatch: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-white/55">
      <span className={"h-2 w-3 rounded-sm " + swatch} />
      {label}
    </span>
  );
}

function KindRow({ label, count, pct }: { label: string; count: number; pct: number }) {
  return (
    <li>
      <div className="flex items-center justify-between text-xs">
        <code className="font-mono text-aqua/90">{label}</code>
        <span className="font-mono text-white/65">{count}</span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-white/[0.06]">
        <div
          className="h-full rounded-full bg-gradient-to-r from-coral via-lilac to-aqua"
          style={{ width: `${pct}%` }}
        />
      </div>
    </li>
  );
}
