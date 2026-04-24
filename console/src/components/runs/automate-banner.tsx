"use client";

/**
 * Automate-this-run banner — RFC-0010 Wave 7 / Phase 4 ticket P4-04.
 *
 * Rendered above the run-detail body when a manual one-shot lands
 * cleanly. Two variants:
 *
 *   - ``wizard``     — primary CTA links to the (not-yet-shipped)
 *                      ``/automations/new`` wizard pre-seeded with
 *                      this play, repo, and source run id.
 *   - ``automated``  — softer note: an enabled automation already
 *                      exists for this play+repo, so we link to its
 *                      detail page instead of nudging the wizard.
 *
 * The component is interactive (Not now / X dismiss buttons fire a
 * client callback supplied by :mod:`automate-banner-controlled`),
 * so the file is a client component. The data-resolution helper
 * :func:`resolveAutomateBannerData` is a pure function — page.tsx
 * (a server component) calls it server-side to compute the props.
 */
 
import Link from "next/link";

import type {
  ApiCatalogPattern,
  ApiLane,
  ApiPipeline,
  ApiPipelineRunWithOutcome,
} from "@/lib/api/client";

// ---------------------------------------------------------------------------
// Data resolution
// ---------------------------------------------------------------------------

/**
 * Banner shape consumed by both the controlled wrapper and the
 * presentational component below. ``null`` from the resolver means
 * "do not render the banner" — the page passes that straight to the
 * wrapper, which short-circuits rendering.
 */
export type AutomateBannerData =
  | {
      variant: "wizard";
      playName: string;
      cadenceText: string;
      wizardHref: string;
    }
  | {
      variant: "automated";
      playName: string;
      automationHref: string;
    };

export type ResolveAutomateBannerInput = {
  run: ApiPipelineRunWithOutcome;
  pipeline: ApiPipeline | null;
  /** Catalog patterns; we look up ``default_trigger`` here. */
  patterns: ApiCatalogPattern[];
  /**
   * Lane projection for this run's repo (or every lane in the
   * workspace — we filter to ``repo_id === pipeline.repo_id``
   * defensively). Used to detect "already automated".
   */
  lanes: ApiLane[];
};

export function resolveAutomateBannerData(
  input: ResolveAutomateBannerInput,
): AutomateBannerData | null {
  const { run, pipeline, patterns, lanes } = input;

  // Edge cases: only manual + succeeded runs get a banner.
  if (run.status !== "succeeded") return null;
  if (run.trigger !== "manual") return null;

  // Repo id is required for the wizard's ``?repo=<id>`` query — and
  // also for the "already automated" lookup. Without it we fail
  // closed (no banner) per the ticket's edge-case list.
  const repoId = pipeline?.repo_id ?? null;
  if (!repoId) return null;

  // Identify the play key used to join into the catalog and lane
  // projection. ``run.lane_id`` is the YAML key (config-side); the
  // pipeline's ``kind`` is the pattern slug (catalog-side). Either
  // can match a catalog row (different patterns use different
  // conventions), so we check both.
  const playKey = run.lane_id ?? pipeline?.kind ?? null;
  if (!playKey) return null;

  const candidates = [pipeline?.kind, run.lane_id].filter(
    (s): s is string => typeof s === "string" && s.length > 0,
  );

  const pattern =
    patterns.find((p) =>
      candidates.some((c) => p.id === c || p.kind === c),
    ) ?? null;
  // Pattern lookup failed (or the catalog API errored upstream and
  // the page passed an empty list) — fail-closed so we don't show a
  // CTA we can't back up.
  if (!pattern) return null;

  // Pattern must support a scheduled or event-driven default. If
  // the only mode is request (``default_trigger`` is null or its
  // ``kind`` is something else), no banner: we'd be inviting the
  // operator to schedule a play that doesn't schedule.
  const trigger = pattern.default_trigger ?? null;
  const triggerKind =
    trigger && typeof trigger === "object" && "kind" in trigger
      ? String((trigger as { kind: unknown }).kind)
      : null;
  if (triggerKind !== "schedule" && triggerKind !== "event") return null;

  const playName = pipeline?.name ?? pattern.name ?? "play";

  // Already-automated check. A lane is "the same automation" when
  // it lives on the same repo, is enabled, isn't a one-shot, and
  // its lane key OR pattern slug matches the play we're looking
  // at. The match is best-effort because ApiLane carries both
  // ``lane_id`` (YAML key) and ``pattern`` (slug) — we accept any
  // combination.
  const existing = lanes.find(
    (l) =>
      l.enabled &&
      l.repo_id === repoId &&
      (l.kind === "event" || l.kind === "schedule") &&
      (l.lane_id === playKey ||
        l.pattern === playKey ||
        l.pattern === pattern.id ||
        l.lane_id === pattern.id),
  );
  if (existing) {
    return {
      variant: "automated",
      playName,
      // TODO(P4-X): ``/automations/<id>`` resolves today (lanes
      // detail), but if Wave 7 renames the route this anchor needs
      // to follow.
      automationHref: `/automations/${encodeURIComponent(existing.id)}`,
    };
  }

  // Wizard variant. The wizard route itself does not exist yet
  // (Phase 1 P1-11 left ``/automations/new`` as a 404 placeholder)
  // — we still emit the link with the seed query params so the
  // route ships ready to consume them.
  const cadenceText = formatCadenceFromPattern(trigger);
  const params = new URLSearchParams({
    play: playKey,
    repo: repoId,
    from_run: run.id,
  });
  // TODO(P4-X): wizard route — ``/automations/new`` is currently a
  // 404 placeholder (Phase 1 P1-11). The link is intentional so the
  // follow-up wizard ticket can pick up ``play``, ``repo`` and
  // ``from_run`` without an extra round of plumbing.
  const wizardHref = `/automations/new?${params.toString()}`;

  return {
    variant: "wizard",
    playName,
    cadenceText,
    wizardHref,
  };
}

// ---------------------------------------------------------------------------
// Cadence formatter
// ---------------------------------------------------------------------------

/**
 * Render a human-readable cadence sentence fragment from a
 * pattern's ``default_trigger``. We deliberately don't pull in a
 * full cron library — Ship's catalog only emits a tiny set of
 * cadences (daily / weekly Monday / on-PR / on-push), so an inline
 * switch covers the cases. Anything we don't recognise falls back
 * to ``"automatically"`` so the banner copy still reads.
 */
export function formatCadenceFromPattern(
  trigger: Record<string, unknown> | null,
): string {
  if (!trigger || typeof trigger !== "object") return "automatically";
  const kind = typeof trigger.kind === "string" ? trigger.kind : null;
  if (kind === "schedule") {
    const cron = typeof trigger.cron === "string" ? trigger.cron : null;
    return formatCron(cron);
  }
  if (kind === "event") {
    const event = typeof trigger.event === "string" ? trigger.event : null;
    if (event === "pull_request") return "on every pull request";
    if (event === "push") return "on every push to main";
    return "on every relevant event";
  }
  return "automatically";
}

const WEEKDAYS = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];

function formatCron(cron: string | null): string {
  if (!cron) return "automatically";
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return "automatically";
  const [min, hr, dom, mon, dow] = parts;
  if (!/^\d+$/.test(hr) || !/^\d+$/.test(min)) return "automatically";
  const time = formatHour(Number(hr), Number(min));
  if (dom === "*" && mon === "*" && dow === "*") {
    return `every day at ${time}`;
  }
  if (dom === "*" && mon === "*" && /^\d+$/.test(dow)) {
    const idx = Number(dow) % 7;
    const day = WEEKDAYS[idx];
    return `every ${day} at ${time}`;
  }
  return "automatically";
}

function formatHour(hr: number, min: number): string {
  const period = hr < 12 ? "am" : "pm";
  const h12 = hr % 12 === 0 ? 12 : hr % 12;
  if (min === 0) return `${h12}${period}`;
  return `${h12}:${min.toString().padStart(2, "0")}${period}`;
}

// ---------------------------------------------------------------------------
// Visual component
// ---------------------------------------------------------------------------

export type AutomateBannerProps = {
  data: AutomateBannerData;
  /** Fired by the "Not now" button and the X close icon. */
  onDismiss?: () => void;
};

export function AutomateBanner({ data, onDismiss }: AutomateBannerProps) {
  if (data.variant === "automated") {
    return (
      <div
        className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl border border-aqua/30 bg-aqua/[0.06] px-4 py-3 text-sm text-white/85 shadow-glow"
        role="status"
      >
        <span aria-hidden className="text-aqua">
          {"\u2728"}
        </span>
        <span className="min-w-0 flex-1">
          This <strong className="font-semibold text-white">{data.playName}</strong>{" "}
          is already automated.
        </span>
        <Link
          href={data.automationHref}
          className="inline-flex items-center gap-1 rounded-full border border-aqua/50 bg-aqua/15 px-3 py-1 text-xs font-bold text-aqua transition hover:bg-aqua/25"
        >
          View automation {"\u2192"}
        </Link>
      </div>
    );
  }

  return (
    <div
      className="relative rounded-2xl border border-aqua/35 bg-aqua/[0.07] px-5 py-4 shadow-glow"
      role="region"
      aria-label="Automate this play"
    >
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Dismiss automate banner"
          className="absolute right-3 top-3 inline-flex h-6 w-6 items-center justify-center rounded-full text-white/55 transition hover:bg-white/10 hover:text-white"
        >
          <span aria-hidden>{"\u00d7"}</span>
        </button>
      )}
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3 pr-8">
        <div className="min-w-0 flex-1">
          <p className="font-display text-sm font-semibold text-white">
            <span aria-hidden className="mr-1.5 text-aqua">
              {"\u2728"}
            </span>
            This <span className="text-aqua">{data.playName}</span> ran cleanly.
            Automate it?
          </p>
          <p className="mt-1 text-xs text-white/70">
            Runs {data.cadenceText} on this repo.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Link
            href={data.wizardHref}
            className="inline-flex items-center gap-1.5 rounded-full bg-aqua px-4 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110"
          >
            {"\u2192"} Set up automation
          </Link>
          {onDismiss && (
            <button
              type="button"
              onClick={onDismiss}
              className="inline-flex items-center rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-xs font-semibold text-white/75 transition hover:bg-white/[0.08] hover:text-white"
            >
              Not now
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
