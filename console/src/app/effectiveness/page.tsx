import { AppShell } from "@/components/app-shell";
import {
  Badge,
  ButtonGhost,
  ButtonPrimary,
  Card,
  CardHeader,
  MockBanner,
  StatTile,
} from "@/components/ui";
import {
  adoptionByPattern,
  effectivenessWeeks,
  workspaces,
} from "@/lib/mock/cloud";

const ws = workspaces[0];

export default function EffectivenessPage() {
  const last = effectivenessWeeks[effectivenessWeeks.length - 1];
  const first = effectivenessWeeks[0];

  // Build SVG sparklines + bar series from the same dataset.
  const leadTimeSeries = effectivenessWeeks.map((w) => w.leadTimeDays);
  const throughputSeries = effectivenessWeeks.map((w) => w.throughputPRs);
  const mttrSeries = effectivenessWeeks.map((w) => w.mttrHours);
  const followSeries = effectivenessWeeks.map((w) => w.retroFollowThroughPct);

  return (
    <AppShell
      kicker={`${ws.name} · effectiveness`}
      title="System effectiveness, week over week"
      actions={
        <>
          <ButtonGhost>Export CSV</ButtonGhost>
          <ButtonGhost>Compare workspaces</ButtonGhost>
          <ButtonPrimary>Schedule weekly digest</ButtonPrimary>
        </>
      }
    >
      <MockBanner />

      <p className="mb-6 max-w-3xl text-sm leading-relaxed text-white/70">
        These four signals are the spine of the Ship operating model from the book: lead time
        (faster = better), throughput (higher = better), MTTR (lower = better) and retro
        follow-through (higher = better). Move them together, not one at the expense of another.
      </p>

      <section className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile
          label="Lead time"
          value={`${last.leadTimeDays}d`}
          delta={{ sign: "down", pct: pct(first.leadTimeDays, last.leadTimeDays) }}
          hint="Commit → prod, P50. Down = better."
        />
        <StatTile
          label="Throughput"
          value={`${last.throughputPRs}/wk`}
          delta={{ sign: "up", pct: pct(last.throughputPRs, first.throughputPRs) }}
          hint="Merged PRs per week."
        />
        <StatTile
          label="MTTR"
          value={`${last.mttrHours}h`}
          delta={{ sign: "down", pct: pct(first.mttrHours, last.mttrHours) }}
          hint="Sev-1 mean time to recovery. Down = better."
        />
        <StatTile
          label="Retro follow-through"
          value={`${last.retroFollowThroughPct}%`}
          delta={{ sign: "up", pct: pct(last.retroFollowThroughPct, first.retroFollowThroughPct) }}
          hint="Approved retro items shipped within 14d."
        />
      </section>

      <section className="mb-6 grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Lead time (days)"
            subtitle="Lower is better · 12-week trend"
          />
          <Sparkline series={leadTimeSeries} stroke="#FF6B6B" lowerIsBetter />
          <WeekTicks weeks={effectivenessWeeks} />
        </Card>

        <Card>
          <CardHeader
            title="Throughput (PRs / week)"
            subtitle="Higher is better · 12-week trend"
          />
          <Sparkline series={throughputSeries} stroke="#76FFD9" />
          <WeekTicks weeks={effectivenessWeeks} />
        </Card>

        <Card>
          <CardHeader
            title="MTTR (hours)"
            subtitle="Lower is better · 12-week trend"
          />
          <Sparkline series={mttrSeries} stroke="#B276FF" lowerIsBetter />
          <WeekTicks weeks={effectivenessWeeks} />
        </Card>

        <Card>
          <CardHeader
            title="Retro follow-through (%)"
            subtitle="Higher is better · share of approved items shipped in 14d"
          />
          <Sparkline series={followSeries} stroke="#FFD66B" />
          <WeekTicks weeks={effectivenessWeeks} />
        </Card>
      </section>

      <section className="grid grid-cols-1 gap-5 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader
            title="Catalog adoption by pattern"
            subtitle={`${adoptionByPattern.length} core patterns · 12 active projects`}
          />
          <ul className="space-y-2.5">
            {adoptionByPattern.map((a) => {
              const pct = Math.round((a.installedIn / a.totalProjects) * 100);
              return (
                <li key={a.pattern} className="text-xs">
                  <div className="mb-1 flex items-center justify-between">
                    <span className="font-mono text-white/85">{a.pattern}</span>
                    <span className="text-white/55">
                      {a.installedIn}/{a.totalProjects} ·{" "}
                      <span className="font-bold text-white">{pct}%</span>
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/[0.05]">
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-aqua via-lilac to-coral"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
        </Card>

        <div className="space-y-5">
          <Card>
            <CardHeader title="Reading the dashboard" />
            <ul className="space-y-3 text-xs leading-relaxed text-white/70">
              <li>
                <Badge tone="ok">healthy</Badge>{" "}
                <span className="ml-1">
                  All four metrics moving in the right direction over 4+ weeks.
                </span>
              </li>
              <li>
                <Badge tone="warn">trade-off</Badge>{" "}
                <span className="ml-1">
                  Throughput up but MTTR also up = you&apos;re shipping faster than you&apos;re
                  catching regressions.
                </span>
              </li>
              <li>
                <Badge tone="err">stalling</Badge>{" "}
                <span className="ml-1">
                  Retro follow-through dropping = action items get approved but not delivered.
                  Look at queue ageing.
                </span>
              </li>
            </ul>
          </Card>

          <Card>
            <CardHeader title="What changed this week" />
            <ul className="space-y-2 text-xs leading-relaxed text-white/75">
              <li>
                Lead time -<b>{(first.leadTimeDays - last.leadTimeDays).toFixed(1)}d</b> vs Wk-12
                — biggest contributor: <code className="font-mono text-aqua/85">pr-and-ci-gate</code>{" "}
                rolled out to last 2 projects.
              </li>
              <li>
                Throughput +<b>{last.throughputPRs - first.throughputPRs}</b> PRs/wk — agent rules
                bundle landed for Cursor users.
              </li>
              <li>
                MTTR -<b>{(first.mttrHours - last.mttrHours).toFixed(1)}h</b> — pipeline self-heal
                pattern adopted in 6/12 projects.
              </li>
            </ul>
          </Card>
        </div>
      </section>
    </AppShell>
  );
}

function pct(from: number, to: number): number {
  if (from === 0) return 0;
  return Math.round((Math.abs(to - from) / from) * 100);
}

function Sparkline({
  series,
  stroke,
  lowerIsBetter,
}: {
  series: number[];
  stroke: string;
  lowerIsBetter?: boolean;
}) {
  const w = 600;
  const h = 140;
  const pad = 14;
  const min = Math.min(...series);
  const max = Math.max(...series);
  const range = max - min || 1;
  const step = (w - pad * 2) / (series.length - 1);

  const points = series.map((v, i) => {
    const x = pad + i * step;
    const y = pad + (h - pad * 2) * (1 - (v - min) / range);
    return [x, y] as const;
  });

  const path = points.map(([x, y], i) => `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${path} L ${points[points.length - 1][0].toFixed(1)} ${h - pad} L ${pad} ${h - pad} Z`;

  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-32 w-full" preserveAspectRatio="none">
      <defs>
        <linearGradient id={`g-${stroke}`} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.35" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      {/* baseline */}
      <line x1={pad} x2={w - pad} y1={h - pad} y2={h - pad} stroke="rgba(255,255,255,0.08)" />
      <path d={area} fill={`url(#g-${stroke})`} />
      <path d={path} fill="none" stroke={stroke} strokeWidth="1.6" strokeLinecap="round" />
      {points.map(([x, y], i) => (
        <circle
          key={i}
          cx={x}
          cy={y}
          r={i === points.length - 1 ? 3 : 1.5}
          fill={i === points.length - 1 ? stroke : "rgba(255,255,255,0.6)"}
        />
      ))}
      <text
        x={w - pad}
        y={pad + 4}
        textAnchor="end"
        fontSize="10"
        fill={stroke}
        fontFamily="ui-monospace, monospace"
      >
        {lowerIsBetter ? "↓ better" : "↑ better"}
      </text>
    </svg>
  );
}

function WeekTicks({ weeks }: { weeks: { weekLabel: string }[] }) {
  return (
    <div className="-mt-1 flex justify-between px-3 font-mono text-[9px] text-white/35">
      {weeks.map((w) => (
        <span key={w.weekLabel}>{w.weekLabel}</span>
      ))}
    </div>
  );
}
