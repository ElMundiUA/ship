import Link from "next/link";

import { Badge, type BadgeTone } from "@/components/ui";
import type {
  ApiPipelineRun,
  RunSummary,
  RunSummaryArtifact,
} from "@/lib/api/client";
import { cn } from "@/lib/cn";

/**
 * One row in the outcome-first ``/runs`` list (RFC-0010 / Wave 6
 * Phase 3 ticket P3-04).
 *
 * Hierarchy (top → bottom):
 *   - Headline + status pill — the ``outcome.outcome_text`` sentence
 *     pattern-authors curate, with a derived fallback when missing
 *     (see ``deriveHeadline`` for the priority chain). Status pill is
 *     to the right.
 *   - Meta row — play name · repo slug · trigger · relative time.
 *   - Outcome detail panel — only renders when there's at least one
 *     of: findings, escalations, artifacts. Each sub-row is its own
 *     line so an operator's eye can land on the exact signal.
 *
 * The whole row links to ``/runs/<run.id>`` (sibling D's detail
 * page). The escalation badge link to ``/inbox?run_id=<run.id>``
 * stops propagation so the parent link doesn't swallow the click.
 *
 * Pure presentational: no data fetching, no router. Drop it inside a
 * ``<ul>``.
 */

export type RunRowProps = {
  run: ApiPipelineRun;
  /** Resolved play / pipeline label for the meta row. */
  playLabel: string;
  /** Resolved ``owner/repo`` slug for the meta row, when known. */
  repoSlug: string | null;
  /** Override "now" for tests / SSR snapshots. */
  referenceDate?: Date;
  className?: string;
};

const STATUS_TONE: Record<string, BadgeTone> = {
  running: "info",
  succeeded: "ok",
  failed: "err",
  cancelled: "neutral",
};

const TRIGGER_LABEL: Record<string, string> = {
  manual: "Manual",
  webhook: "Webhook",
  cron: "Scheduled",
  onboarding: "Onboarding",
};

function statusLabel(status: string): string {
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function pluralize(n: number, one: string, many?: string): string {
  return n === 1 ? `${n} ${one}` : `${n} ${many ?? `${one}s`}`;
}

/**
 * Build the headline shown on the row. Pattern-authors set
 * ``outcome.outcome_text`` directly; everything else is a fallback
 * chain so legacy / un-instrumented runs still read as outcomes
 * rather than naked statuses.
 */
export function deriveHeadline(run: ApiPipelineRun): string {
  const outcome = run.outcome ?? {};
  const text = outcome.outcome_text?.trim();
  if (text) return text;

  if (typeof outcome.findings_count === "number" && outcome.findings_count > 0) {
    return pluralize(outcome.findings_count, "finding");
  }

  const artifacts = outcome.artifacts ?? [];
  if (artifacts.length > 0) {
    const counts = new Map<string, number>();
    for (const a of artifacts) {
      counts.set(a.type, (counts.get(a.type) ?? 0) + 1);
    }
    const parts = [...counts.entries()].map(([type, n]) =>
      pluralize(n, formatArtifactType(type)),
    );
    return `${parts.join(" · ")} produced`;
  }

  if (outcome.requires_approval) return "Awaiting approval";

  const summary = run.summary?.trim();
  if (summary) return summary;

  return `Run ${statusLabel(run.status)}`;
}

function formatArtifactType(type: string): string {
  // Convert the wire-form ``type`` (``pr`` / ``issue`` / ``comment`` /
  // ``doc`` / …) into a display-friendly singular noun. We special-
  // case the obvious initialisms; anything else falls through with
  // basic title-casing.
  if (type === "pr") return "PR";
  if (type === "issue") return "issue";
  if (type === "comment") return "comment";
  if (type === "doc") return "doc";
  return type.replace(/[_-]+/g, " ").toLowerCase();
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, max - 1).trimEnd()}…`;
}

function formatRelative(iso: string | null, referenceDate?: Date): string {
  if (!iso) return "no timestamp";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return iso;
  const now = (referenceDate ?? new Date()).getTime();
  const sec = Math.max(1, Math.round((now - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

export function RunRow({
  run,
  playLabel,
  repoSlug,
  referenceDate,
  className,
}: RunRowProps) {
  const outcome: RunSummary = run.outcome ?? {};
  const headline = deriveHeadline(run);
  const tone = STATUS_TONE[run.status] ?? "neutral";
  const triggerLabel = TRIGGER_LABEL[run.trigger] ?? run.trigger;
  const when = formatRelative(
    run.started_at ?? run.created_at,
    referenceDate,
  );

  const findings = outcome.findings_count ?? 0;
  const findingsBreakdown = outcome.findings_by_severity ?? {};
  const escalations = outcome.escalations ?? [];
  const artifacts = outcome.artifacts ?? [];

  const showDetails =
    findings > 0 || escalations.length > 0 || artifacts.length > 0;

  return (
    <Link
      href={`/runs/${run.id}`}
      aria-label={`Run: ${headline}`}
      className="block rounded-2xl focus:outline-none focus-visible:ring-2 focus-visible:ring-aqua/60 focus-visible:ring-offset-2 focus-visible:ring-offset-ink"
    >
      <div
        className={cn(
          "rounded-2xl border border-white/10 bg-white/[0.04] p-4 transition hover:border-white/20 hover:bg-white/[0.07]",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-3">
          <h3 className="min-w-0 flex-1 text-base font-semibold leading-tight text-white">
            {headline}
          </h3>
          <Badge tone={tone} dot={run.status === "running"}>
            {statusLabel(run.status)}
          </Badge>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-white/55">
          <span className="font-semibold text-white/70">{playLabel}</span>
          {repoSlug && (
            <>
              <Sep />
              <code className="font-mono text-white/65">{repoSlug}</code>
            </>
          )}
          <Sep />
          <span>{triggerLabel}</span>
          <Sep />
          <span title={run.started_at ?? run.created_at}>{when}</span>
        </div>

        {showDetails && (
          <div className="mt-3 space-y-1.5 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2 text-xs text-white/75">
            {findings > 0 && (
              <FindingsLine
                count={findings}
                breakdown={findingsBreakdown}
              />
            )}
            {escalations.length > 0 && (
              <EscalationsLine runId={run.id} count={escalations.length} />
            )}
            {artifacts.length > 0 && <ArtifactsLine artifacts={artifacts} />}
          </div>
        )}
      </div>
    </Link>
  );
}

function Sep() {
  return <span aria-hidden className="text-white/25">·</span>;
}

function FindingsLine({
  count,
  breakdown,
}: {
  count: number;
  breakdown: NonNullable<RunSummary["findings_by_severity"]>;
}) {
  const high = breakdown?.high ?? 0;
  const med = breakdown?.medium ?? 0;
  const low = breakdown?.low ?? 0;
  const crit = breakdown?.critical ?? 0;
  const parts: string[] = [];
  if (crit > 0) parts.push(`${crit} critical`);
  if (high > 0) parts.push(`${high} high`);
  if (med > 0) parts.push(`${med} medium`);
  if (low > 0) parts.push(`${low} low`);
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span aria-hidden className="text-white/55">◐</span>
      <span className="font-semibold text-white">
        {pluralize(count, "finding")}
      </span>
      {parts.length > 0 && (
        <span className="text-white/55">({parts.join(" · ")})</span>
      )}
    </div>
  );
}

function EscalationsLine({
  runId,
  count,
}: {
  runId: string;
  count: number;
}) {
  // Forward-compat URL: the inbox list filtering by ``run_id`` is not
  // wired server-side yet (sibling-A may add ``?run_id=`` to the
  // inbox list query in a follow-up). Rendering it now means the
  // moment the BE adds support, every existing run row light-paths
  // into the right view; until then the inbox list ignores the
  // unknown param and the operator lands on the unfiltered queue —
  // still a meaningful drill, just not scoped.
  return (
    <Link
      href={`/inbox?run_id=${encodeURIComponent(runId)}`}
      onClick={(e) => e.stopPropagation()}
      className="flex flex-wrap items-center gap-1.5 text-coral hover:text-coral/80"
    >
      <span aria-hidden>⚠</span>
      <span className="font-semibold">
        {pluralize(count, "escalation")}
      </span>
      <span className="text-white/55">→ Inbox</span>
    </Link>
  );
}

function ArtifactsLine({
  artifacts,
}: {
  artifacts: RunSummaryArtifact[];
}) {
  const visible = artifacts.slice(0, 3);
  const remainder = artifacts.length - visible.length;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span aria-hidden className="text-white/55">◇</span>
      <span className="font-semibold text-white/75">
        {pluralize(artifacts.length, "artifact")}
      </span>
      <span className="text-white/45">·</span>
      <div className="flex flex-wrap items-center gap-1.5">
        {visible.map((a, i) => (
          <ArtifactChip key={`${a.type}-${i}`} artifact={a} />
        ))}
        {remainder > 0 && (
          <span className="text-[10px] font-semibold uppercase tracking-wider text-white/45">
            +{remainder} more
          </span>
        )}
      </div>
    </div>
  );
}

function ArtifactChip({ artifact }: { artifact: RunSummaryArtifact }) {
  const label = `${artifact.type}: ${truncate(artifact.title, 40)}`;
  const cls =
    "inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-2 py-0.5 text-[10px] font-medium text-white/75";
  if (artifact.ref) {
    return (
      <a
        href={artifact.ref}
        target="_blank"
        rel="noreferrer"
        onClick={(e) => e.stopPropagation()}
        className={cn(cls, "hover:border-aqua/40 hover:text-aqua")}
      >
        {label}
      </a>
    );
  }
  return <span className={cls}>{label}</span>;
}
