import Link from "next/link";

import { Badge } from "@/components/ui";
import type {
  ApiCatalogPattern,
  ApiLaneCatalogEntry,
} from "@/lib/api/client";

import {
  PLAY_CATEGORIES,
  type PlayCategoryDef,
  type PlaySubcategoryDef,
} from "./category-sidebar";

/**
 * Server-rendered body for the Play detail drawer (RFC-0010 / Wave 7
 * Phase 4 ticket P4-02).
 *
 * Sections (in render order):
 *
 *   1. **Header** — category chip · critical badge · business name ·
 *      tagline (the pattern's ``description`` first sentence).
 *   2. **What it does** — full multi-paragraph description.
 *   3. **What it produces** — bullet list from
 *      ``pattern.outputs`` (frontmatter ``outputs:``); falls back to a
 *      single line derived from the play's mode.
 *   4. **What's included** — bullet list of pattern.include entries
 *      (e.g. ``common-base``). Hidden when the list is empty.
 *   5. **Default execution mode** — mode chip + a contextual hint
 *      (``"PR opened or updated"`` for event-driven, the cron for
 *      scheduled, ``"Run from /plays manually"`` for on-demand).
 *   6. **Inbox routing** — profile name + a link to the routing
 *      settings page.
 *   7. **Footer CTAs** — Run now (link to grid CTA) · Automate ·
 *      Read full pattern.
 *
 * Lane-only Plays (no ``pattern``) get a slimmed-down view: header
 * + what-it-does + execution-mode + automate CTA. They lack
 * frontmatter so the ``outputs`` / ``include`` / ``inbox_profile``
 * sections are skipped — these are tracked under sibling B's
 * follow-up to surface lane-recipe metadata too.
 */

export type PlayDetailInput =
  | { kind: "request"; id: string; pattern: ApiCatalogPattern }
  | { kind: "lane"; id: string; entry: ApiLaneCatalogEntry };

export function PlayDetailDrawer({ play }: { play: PlayDetailInput }) {
  const meta = describePlay(play);
  return (
    <div className="flex h-full flex-col">
      <Header meta={meta} />
      <div className="space-y-6 px-6 py-4">
        <Section title="What it does">
          <Paragraphs text={meta.description} />
        </Section>

        <Section title="What it produces">
          <Outputs meta={meta} />
        </Section>

        {meta.include.length > 0 && (
          <Section title="What’s included">
            <ul className="space-y-1 text-[12.5px] text-white/75">
              {meta.include.map((entry) => (
                <li key={entry} className="flex items-start gap-2">
                  <span aria-hidden className="mt-1.5 h-1 w-1 shrink-0 rounded-full bg-white/40" />
                  <code className="font-mono text-[12px] text-white/85">
                    {entry}
                  </code>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="Default execution mode">
          <ExecutionMode meta={meta} />
        </Section>

        <Section title="Inbox routing">
          <InboxRouting profile={meta.inboxProfile} />
        </Section>
      </div>
      <Footer meta={meta} />
    </div>
  );
}

// -- presentation primitives --------------------------------------------------

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section>
      <h3 className="mb-2 text-[10px] font-bold uppercase tracking-[0.18em] text-white/45">
        {title}
      </h3>
      <div className="text-[12.5px] leading-relaxed text-white/80">
        {children}
      </div>
    </section>
  );
}

function Paragraphs({ text }: { text: string }) {
  const paragraphs = text.split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  if (paragraphs.length === 0) {
    return (
      <p className="text-white/55">
        This play has no extended description yet.
      </p>
    );
  }
  return (
    <div className="space-y-2.5">
      {paragraphs.map((p, i) => (
        <p key={i} className="text-white/80">
          {p}
        </p>
      ))}
    </div>
  );
}

function Header({ meta }: { meta: PlayMeta }) {
  return (
    <div className="border-b border-white/10 px-6 pb-4 pt-5">
      <div className="flex flex-wrap items-center gap-1.5">
        {meta.category && (
          <Badge tone="info">{meta.category.label}</Badge>
        )}
        {meta.subcategory && (
          <Badge tone="neutral">{meta.subcategory.label}</Badge>
        )}
        {meta.critical && (
          <Badge tone="err" dot>
            Critical
          </Badge>
        )}
        {meta.kind === "lane" && (
          <Badge tone="neutral">Lane recipe</Badge>
        )}
      </div>
      <h2 className="mt-2 font-display text-xl font-bold leading-tight text-white">
        {meta.title}
      </h2>
      {meta.tagline && (
        <p className="mt-1.5 text-[13px] leading-snug text-white/65">
          {meta.tagline}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] text-white/40">
        <code className="font-mono">{meta.id}</code>
        {meta.version && <span>v{meta.version}</span>}
      </div>
    </div>
  );
}

function Outputs({ meta }: { meta: PlayMeta }) {
  if (meta.outputs.length > 0) {
    return (
      <ul className="space-y-1.5">
        {meta.outputs.map((o, i) => (
          <li
            key={`${o.type}-${i}`}
            className="flex items-start gap-2 text-white/80"
          >
            <span
              aria-hidden
              className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-aqua/60"
            />
            <span className="min-w-0">
              <span className="font-semibold text-white">{o.title}</span>
              <span className="ml-1.5 text-[11px] uppercase tracking-wider text-white/45">
                {o.type}
              </span>
            </span>
          </li>
        ))}
      </ul>
    );
  }
  // Fallback per ticket spec — keeps the section meaningful when
  // the pattern hasn't (yet) declared an ``outputs:`` block.
  const fallback =
    meta.mode === "event-driven"
      ? "Posts findings as PR comments."
      : meta.mode === "scheduled"
        ? `Runs in ${meta.mode} mode and emits an outcome summary in /runs.`
        : "Posts findings as PR comments / runs in on-demand mode.";
  return <p className="text-white/65">{fallback}</p>;
}

function ExecutionMode({ meta }: { meta: PlayMeta }) {
  const modeTone =
    meta.mode === "event-driven"
      ? "info"
      : meta.mode === "scheduled"
        ? "ok"
        : "neutral";
  let hint: string;
  if (meta.mode === "event-driven") {
    hint = meta.eventTrigger
      ? `Triggered on ${meta.eventTrigger}.`
      : "Triggered on PR opened or updated.";
  } else if (meta.mode === "scheduled") {
    hint = meta.cron
      ? `Cron: ${meta.cron}`
      : "Runs on a schedule (cadence configured per workspace).";
  } else {
    hint = "Run from /plays manually, or wire it through Automate.";
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Badge tone={modeTone}>{meta.mode}</Badge>
      <span className="text-[12px] text-white/65">{hint}</span>
    </div>
  );
}

function InboxRouting({ profile }: { profile: string | null }) {
  return (
    <div className="flex flex-wrap items-center gap-2 text-[12.5px] text-white/75">
      {profile ? (
        <code className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-0.5 font-mono text-[12px] text-white/85">
          {profile}
        </code>
      ) : (
        <span className="text-white/55">
          Default routing — no per-play override.
        </span>
      )}
      <Link
        href="/settings?tab=members"
        className="text-[11px] font-semibold text-aqua hover:text-aqua/80"
      >
        Who answers? →
      </Link>
    </div>
  );
}

function Footer({ meta }: { meta: PlayMeta }) {
  // The drawer's "Run now" CTA is intentionally a link back to the
  // page with ``?play=`` cleared — the actual dispatch UI lives on
  // the card (it reuses the existing PatternForm). Keeping this as
  // a link rather than re-mounting the form here means we don't
  // duplicate the dispatch state machine in two places.
  const automateHref = `/automations/new?play=${encodeURIComponent(meta.id)}`;
  return (
    <div className="mt-auto border-t border-white/10 bg-white/[0.02] px-6 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          {meta.canRunNow && (
            <Link
              href={`/plays?play=${encodeURIComponent(meta.id)}#run`}
              className="rounded-full border border-aqua/60 bg-aqua/80 px-3.5 py-1 text-[11px] font-bold text-ink hover:bg-aqua"
            >
              Run now
            </Link>
          )}
          <Link
            href={automateHref}
            className={
              "rounded-full border px-3.5 py-1 text-[11px] font-bold transition " +
              (meta.canRunNow
                ? "border-white/20 bg-white/[0.04] text-white/80 hover:border-white/35 hover:text-white"
                : "border-aqua/60 bg-aqua/80 text-ink hover:bg-aqua")
            }
          >
            Automate
          </Link>
        </div>
        <Link
          href={`/catalog/${encodeURIComponent(meta.id)}`}
          className="text-[11px] font-semibold text-white/55 hover:text-white"
        >
          Read full pattern →
        </Link>
      </div>
    </div>
  );
}

// -- shape normalisation -------------------------------------------------------

type PlayMeta = {
  id: string;
  kind: "request" | "lane";
  title: string;
  tagline: string;
  description: string;
  category: PlayCategoryDef | null;
  subcategory: PlaySubcategoryDef | null;
  critical: boolean;
  outputs: NonNullable<ApiCatalogPattern["outputs"]>;
  include: string[];
  mode: "event-driven" | "scheduled" | "on-demand";
  eventTrigger: string | null;
  cron: string | null;
  inboxProfile: string | null;
  version: string | null;
  canRunNow: boolean;
};

function describePlay(play: PlayDetailInput): PlayMeta {
  if (play.kind === "request") {
    const p = play.pattern;
    const category = lookupCategory(p.category);
    const subcategory =
      category?.id === "health_checks"
        ? lookupSubcategory(category, p.subcategory)
        : null;
    const description = p.description?.trim() ?? "";
    const tagline = description.split(/\.\s+/)[0]?.trim() ?? "";
    const mode = resolveModeFromPattern(p);
    return {
      id: p.id,
      kind: "request",
      title: p.name ?? p.id,
      tagline,
      description,
      category,
      subcategory,
      critical: p.critical === true,
      outputs: p.outputs ?? [],
      include: p.include ?? [],
      mode,
      eventTrigger: extractEventTrigger(p.default_trigger),
      cron: extractCron(p.default_trigger),
      inboxProfile: p.inbox_profile ?? null,
      version: p.version,
      canRunNow: (p.modes ?? []).includes("request"),
    };
  }
  const e = play.entry;
  const description = e.summary?.trim() ?? "";
  const tagline = description.split(/\.\s+/)[0]?.trim() ?? "";
  const mode: PlayMeta["mode"] = e.event
    ? "event-driven"
    : e.schedule
      ? "scheduled"
      : "on-demand";
  return {
    id: play.id,
    kind: "lane",
    title: e.title,
    tagline,
    description,
    category: null,
    subcategory: null,
    critical: false,
    outputs: [],
    include: [],
    mode,
    eventTrigger: e.event,
    cron: e.schedule,
    inboxProfile: null,
    version: null,
    canRunNow: false,
  };
}

function lookupCategory(id: string | null | undefined): PlayCategoryDef | null {
  if (!id) return null;
  return PLAY_CATEGORIES.find((c) => c.id === id) ?? null;
}

function lookupSubcategory(
  category: PlayCategoryDef,
  id: string | null | undefined,
): PlaySubcategoryDef | null {
  if (!id || !category.subcategories) return null;
  return category.subcategories.find((s) => s.id === id) ?? null;
}

function resolveModeFromPattern(p: ApiCatalogPattern): PlayMeta["mode"] {
  const modes = p.modes ?? [];
  if (modes.includes("event")) return "event-driven";
  if (modes.includes("schedule")) return "scheduled";
  if (modes.includes("request")) return "on-demand";
  if (modes.includes("lane")) return "scheduled";
  return "on-demand";
}

function extractEventTrigger(
  trig: Record<string, unknown> | null | undefined,
): string | null {
  if (!trig) return null;
  const event = trig.event ?? trig.events ?? trig.on;
  if (typeof event === "string") return event;
  if (Array.isArray(event)) return event.join(", ");
  return null;
}

function extractCron(
  trig: Record<string, unknown> | null | undefined,
): string | null {
  if (!trig) return null;
  const cron = trig.cron ?? trig.schedule;
  if (typeof cron === "string") return cron;
  return null;
}
