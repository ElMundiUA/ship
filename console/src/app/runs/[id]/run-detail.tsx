/**
 * Outcome-first run detail view (RFC-0010 Wave 6 / Phase 3 ticket
 * P3-05).
 *
 * Replaces the legacy ``console/src/app/pipelines/run-detail-view.tsx``
 * — which read as a flat metrics dump — with the structure called
 * out in the planning doc:
 *
 *   - kicker + outcome-led title + meta subtitle
 *   - stacked sections (Outcome, Artifacts, Findings, Escalations,
 *     Raw payload) with anchor ids so individual sections can be
 *     deeplinked (``/runs/<id>#findings``)
 *   - right-rail "About this run" card mirroring the inbox detail
 *     page's convention
 *   - header actions: Re-run (POST → server action) + View in Plays
 *
 * **Why stacked-sections instead of tabs.** The user query gave us
 * the choice; we picked stacked because (a) it matches the inbox
 * detail page's anchor-driven convention so the IA stays coherent,
 * (b) every section is shareable via ``#anchor`` without JS, and
 * (c) it degrades cleanly when the run is sparse (a play that only
 * authored ``outcome_text`` reads as one short outcome card with
 * "no artifacts" placeholders below — tabs would surface empty
 * panels behind unclickable triggers).
 */

import Link from "next/link";

import { ArtifactCard } from "@/components/runs/artifact-card";
import { EscalationCard } from "@/components/runs/escalation-card";
import { Badge, Card, CardHeader } from "@/components/ui";
import type {
  ApiPipeline,
  ApiPipelineRunWithOutcome,
  ApiRunEscalation,
  RunSummary,
  RunSummaryFindingsBySeverity,
} from "@/lib/api/client";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function runStatusTone(
  status: string,
): "ok" | "err" | "info" | "warn" | "neutral" {
  if (status === "succeeded") return "ok";
  if (status === "failed") return "err";
  if (status === "running" || status === "queued") return "info";
  if (status === "cancelled") return "warn";
  return "neutral";
}

function formatAbsolute(iso: string | null): string {
  if (!iso) return "\u2014";
  const d = new Date(iso);
  if (!Number.isFinite(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

function formatRelative(iso: string | null): string | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return iso;
  const diff = Date.now() - t;
  const sec = Math.round(diff / 1000);
  if (sec < 30) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  if (days === 1) return "yesterday";
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
}

function formatDuration(
  started: string | null,
  finished: string | null,
): string | null {
  if (!started || !finished) return null;
  const a = new Date(started).getTime();
  const b = new Date(finished).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null;
  const sec = Math.round((b - a) / 1000);
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return remSec ? `${min}m ${remSec}s` : `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return remMin ? `${hr}h ${remMin}m` : `${hr}h`;
}

function shortId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}\u2026` : id;
}

function severityRows(
  buckets: RunSummaryFindingsBySeverity,
): Array<{ label: string; count: number; tone: "err" | "warn" | "info" | "neutral" }> {
  return [
    { label: "Critical", count: buckets.critical ?? 0, tone: "err" },
    { label: "High", count: buckets.high ?? 0, tone: "err" },
    { label: "Medium", count: buckets.medium ?? 0, tone: "warn" },
    { label: "Low", count: buckets.low ?? 0, tone: "info" },
  ];
}

function findingsSummaryLine(outcome: RunSummary): string | null {
  const total = outcome.findings_count ?? null;
  const buckets = outcome.findings_by_severity ?? null;
  if (total === null && !buckets) return null;
  const parts: string[] = [];
  if (buckets) {
    for (const r of severityRows(buckets)) {
      if (r.count > 0) parts.push(`${r.count} ${r.label.toLowerCase()}`);
    }
  }
  const headTotal = total ?? parts.reduce((acc, p) => acc + Number.parseInt(p, 10), 0);
  if (parts.length === 0) return `${headTotal} finding${headTotal === 1 ? "" : "s"}`;
  return `${headTotal} finding${headTotal === 1 ? "" : "s"} (${parts.join(" \u00b7 ")})`;
}

// ---------------------------------------------------------------------------
// Top-level view
// ---------------------------------------------------------------------------

export type RunDetailViewProps = {
  workspaceId: string;
  workspaceSlug?: string;
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
  escalations: ApiRunEscalation[];
  /** When the escalation fetch errored we render a degraded note. */
  escalationsError: boolean;
};

export function RunDetail({
  workspaceId,
  run,
  pipeline,
  escalations,
  escalationsError,
}: RunDetailViewProps) {
  const outcome: RunSummary = run.outcome ?? {};
  const playName = pipeline?.name ?? "Pipeline";

  // Title fallback ladder (per ticket spec):
  //   outcome.outcome_text > legacy run.summary > "Run {status}"
  const title =
    (outcome.outcome_text && outcome.outcome_text.trim()) ||
    (run.summary && run.summary.trim()) ||
    `Run ${run.status}`;

  const duration = formatDuration(run.started_at, run.finished_at);
  const startedRel = formatRelative(run.started_at);
  const finishedRel = formatRelative(run.finished_at);

  return (
    <>
      <Breadcrumb runId={run.id} playName={playName} />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <HeaderCard
            run={run}
            pipeline={pipeline}
            playName={playName}
            workspaceId={workspaceId}
            title={title}
            outcome={outcome}
            startedRel={startedRel}
            finishedRel={finishedRel}
            duration={duration}
          />
          <OutcomeSection outcome={outcome} legacyShown={title !== `Run ${run.status}` && !outcome.outcome_text} />
          <ArtifactsSection outcome={outcome} />
          <FindingsSection outcome={outcome} />
          <EscalationsSection
            outcome={outcome}
            escalations={escalations}
            escalationsError={escalationsError}
          />
          <RawPayloadSection run={run} outcome={outcome} />
        </div>

        <aside className="space-y-4">
          <AboutCard
            run={run}
            pipeline={pipeline}
            duration={duration}
          />
        </aside>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Breadcrumb
// ---------------------------------------------------------------------------

function Breadcrumb({ runId, playName }: { runId: string; playName: string }) {
  return (
    <nav className="mb-4 max-w-2xl text-xs text-white/55" aria-label="Breadcrumb">
      <Link href="/runs" className="font-semibold text-aqua hover:underline">
        Runs
      </Link>
      <span className="text-white/35"> / </span>
      <span className="text-white/70">{playName}</span>
      <span className="text-white/35"> / </span>
      <code className="rounded bg-white/[0.06] px-1 py-0.5 text-[10px] text-white/70">
        {shortId(runId)}
      </code>
    </nav>
  );
}

// ---------------------------------------------------------------------------
// Header card
// ---------------------------------------------------------------------------

function HeaderCard({
  run,
  pipeline,
  playName,
  workspaceId,
  title,
  outcome,
  startedRel,
  finishedRel,
  duration,
}: {
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
  playName: string;
  workspaceId: string;
  title: string;
  outcome: RunSummary;
  startedRel: string | null;
  finishedRel: string | null;
  duration: string | null;
}) {
  const subtitleParts: string[] = [playName];
  if (pipeline?.repo_full_name) subtitleParts.push(pipeline.repo_full_name);
  subtitleParts.push(run.trigger);
  if (startedRel) {
    const tail = finishedRel ? `${startedRel} \u2192 ${finishedRel}` : `started ${startedRel}`;
    subtitleParts.push(tail);
  }
  if (duration) subtitleParts.push(duration);

  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-white/55">
              Run
            </span>
            <Badge tone={runStatusTone(run.status)} dot>
              {run.status}
            </Badge>
            {outcome.requires_approval && (
              <Badge tone="warn">Approval required</Badge>
            )}
          </div>
          <h1 className="mt-2 break-words font-display text-xl font-bold text-white">
            {title}
          </h1>
          <p className="mt-1 text-xs text-white/55">
            {subtitleParts.join(" \u00b7 ")}
          </p>
        </div>

        <HeaderActions
          run={run}
          pipeline={pipeline}
          workspaceId={workspaceId}
        />
      </div>
    </Card>
  );
}

function HeaderActions({
  run,
  pipeline,
  workspaceId,
}: {
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
  workspaceId: string;
}) {
  const canRerun = Boolean(run.pipeline_id);
  const laneId = run.lane_id ?? null;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-2">
      <form
        action={`/api/runs/${encodeURIComponent(run.id)}/rerun`}
        method="POST"
      >
        <input type="hidden" name="ws" value={workspaceId} />
        {pipeline?.id && (
          <input type="hidden" name="pipeline" value={pipeline.id} />
        )}
        <input type="hidden" name="run" value={run.id} />
        <button
          type="submit"
          disabled={!canRerun}
          title={
            canRerun
              ? "Dispatch this pipeline again with a 'Re-run of …' note."
              : "Run is not bound to a pipeline; cannot re-run."
          }
          className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-4 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:opacity-40"
        >
          {"\u21BB"} Re-run
        </button>
      </form>
      {laneId ? (
        <Link
          href={`/plays?play=${encodeURIComponent(laneId)}`}
          className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]"
        >
          View in Plays
        </Link>
      ) : (
        <Link
          href="/plays"
          className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/85 transition hover:bg-white/[0.08]"
        >
          Browse Plays
        </Link>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Outcome section
// ---------------------------------------------------------------------------

function OutcomeSection({
  outcome,
  legacyShown,
}: {
  outcome: RunSummary;
  legacyShown: boolean;
}) {
  const hasOutcomeText = Boolean(outcome.outcome_text?.trim());
  const findingsLine = findingsSummaryLine(outcome);

  return (
    <Card id="outcome">
      <CardHeader
        title="Outcome"
        subtitle="Pattern-authored result of this run."
      />
      {hasOutcomeText ? (
        <p className="text-sm text-white/85">{outcome.outcome_text}</p>
      ) : (
        <p className="text-sm text-white/55">
          {"\u2014"}{" "}
          <span className="text-[11px] italic text-white/45">
            {legacyShown
              ? "Pattern did not author an outcome line; legacy summary above."
              : "Pattern did not author an outcome line."}
          </span>
        </p>
      )}
      {outcome.headline && (
        <p className="mt-2 text-xs text-white/55">{outcome.headline}</p>
      )}
      {findingsLine && (
        <p className="mt-3 text-xs text-white/65">{findingsLine}</p>
      )}
      {outcome.requires_approval && (
        <div className="mt-4 rounded-xl border border-sun/30 bg-sun/[0.06] px-3 py-2 text-xs text-sun/95">
          <strong className="font-semibold">Run requires approval.</strong>{" "}
          See the escalation list below for the linked inbox item.{" "}
          <Link href="#escalations" className="font-semibold underline">
            Jump to escalations
          </Link>
          .
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Artifacts section
// ---------------------------------------------------------------------------

function ArtifactsSection({ outcome }: { outcome: RunSummary }) {
  const artifacts = outcome.artifacts ?? [];
  return (
    <Card id="artifacts">
      <CardHeader
        title="Artifacts"
        subtitle="What this run produced."
      />
      {artifacts.length === 0 ? (
        <p className="text-xs text-white/55">
          No artifacts produced by this run.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {artifacts.map((artifact, idx) => (
            <ArtifactCard
              key={`${artifact.type}-${artifact.ref ?? idx}`}
              artifact={artifact}
            />
          ))}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Findings section
// ---------------------------------------------------------------------------

function FindingsSection({ outcome }: { outcome: RunSummary }) {
  const buckets = outcome.findings_by_severity ?? null;
  const total = outcome.findings_count ?? null;
  if (!buckets && total === null) return null;

  return (
    <Card id="findings">
      <CardHeader
        title="Findings"
        subtitle="Aggregate counts by severity. Drill into escalations or downstream tools for individual findings."
      />
      {buckets ? (
        <div className="overflow-hidden rounded-lg border border-white/10">
          <table className="w-full text-left text-xs">
            <thead className="bg-white/[0.04] text-[10px] uppercase tracking-widest text-white/55">
              <tr>
                <th className="px-3 py-2 font-semibold">Severity</th>
                <th className="px-3 py-2 text-right font-semibold">Count</th>
              </tr>
            </thead>
            <tbody>
              {severityRows(buckets).map((row) => (
                <tr
                  key={row.label}
                  className="border-t border-white/5 text-white/80"
                >
                  <td className="px-3 py-2">
                    <Badge tone={row.tone}>{row.label}</Badge>
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-white/90">
                    {row.count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-xs text-white/65">
          {total} finding{total === 1 ? "" : "s"} reported (no severity
          breakdown).
        </p>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Escalations section
// ---------------------------------------------------------------------------

function EscalationsSection({
  outcome,
  escalations,
  escalationsError,
}: {
  outcome: RunSummary;
  escalations: ApiRunEscalation[];
  escalationsError: boolean;
}) {
  const hints = outcome.escalations ?? [];
  return (
    <Card id="escalations">
      <CardHeader
        title="Escalations"
        subtitle="Inbox items linked to this run."
      />
      {hints.length > 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {hints.map((hint, idx) => (
            <span
              key={`${hint.type}-${hint.reason}-${idx}`}
              className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-2.5 py-1 text-[11px] text-white/75"
              title={`Pattern-emitted hint: ${hint.type} → ${hint.reason}`}
            >
              <span className="font-semibold uppercase tracking-wider text-white/55">
                {hint.type}
              </span>
              <span className="text-white/40">{"\u2192"}</span>
              <code className="rounded bg-white/[0.06] px-1 py-0.5 text-[10px]">
                {hint.reason}
              </code>
            </span>
          ))}
        </div>
      )}
      {escalationsError ? (
        <p className="text-xs text-white/55">
          Couldn{"\u2019"}t load linked escalations. The pattern hints above
          are best-effort diagnostics.
        </p>
      ) : escalations.length === 0 ? (
        <p className="text-xs text-white/55">
          No escalations linked to this run.
        </p>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2">
          {escalations.map((escalation) => (
            <EscalationCard key={escalation.id} escalation={escalation} />
          ))}
        </div>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Raw payload section
// ---------------------------------------------------------------------------

function RawPayloadSection({
  run,
  outcome,
}: {
  run: ApiPipelineRunWithOutcome;
  outcome: RunSummary;
}) {
  const metrics =
    run.payload &&
    typeof run.payload.metrics === "object" &&
    run.payload.metrics !== null &&
    !Array.isArray(run.payload.metrics)
      ? (run.payload.metrics as Record<string, unknown>)
      : null;
  const json = JSON.stringify(
    {
      payload: run.payload ?? {},
      outcome,
      ...(metrics ? { metrics } : {}),
    },
    null,
    2,
  );
  return (
    <Card id="raw" padded={false}>
      <details className="group">
        <summary className="flex cursor-pointer items-center justify-between gap-2 px-5 py-4 text-[10px] uppercase tracking-widest text-white/55 hover:text-white/85">
          <span>Raw payload (debug)</span>
          <span className="text-xs text-white/40 transition group-open:rotate-90">
            {"\u203A"}
          </span>
        </summary>
        <pre className="max-h-72 overflow-auto border-t border-white/10 bg-ink/80 px-5 py-4 font-mono text-[11px] leading-5 text-white/80">
          {json}
        </pre>
      </details>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Right rail: About this run
// ---------------------------------------------------------------------------

function AboutCard({
  run,
  pipeline,
  duration,
}: {
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
  duration: string | null;
}) {
  const laneId = run.lane_id ?? null;
  const triggeredBy =
    run.payload && typeof run.payload["triggered_by"] === "string"
      ? (run.payload["triggered_by"] as string)
      : null;
  return (
    <Card>
      <CardHeader title="About this run" />
      <dl className="space-y-2 text-xs">
        <Row label="Status">
          <Badge tone={runStatusTone(run.status)} dot>
            {run.status}
          </Badge>
        </Row>
        {duration && <Row label="Duration">{duration}</Row>}
        <Row label="Trigger">{run.trigger}</Row>
        {triggeredBy && <Row label="Triggered by">{triggeredBy}</Row>}
        <Row label="Started">{formatAbsolute(run.started_at)}</Row>
        <Row label="Finished">{formatAbsolute(run.finished_at)}</Row>
        <Row label="Run ID">
          <code className="rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[10px] text-white/85">
            {shortId(run.id)}
          </code>
        </Row>
        {pipeline && (
          <Row label="Pipeline">
            {laneId ? (
              <Link
                href={`/automations/${encodeURIComponent(laneId)}`}
                className="text-aqua hover:underline"
              >
                {pipeline.name}
              </Link>
            ) : (
              <span className="text-white/85">{pipeline.name}</span>
            )}
          </Row>
        )}
      </dl>
    </Card>
  );
}

function Row({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-white/45">
        {label}
      </dt>
      <dd className="min-w-0 text-right text-xs text-white/85">{children}</dd>
    </div>
  );
}
