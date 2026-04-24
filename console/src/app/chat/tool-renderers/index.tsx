"use client";

/**
 * Per-tool result renderers for the Navigator chat (Phase 6 Wave C, FE).
 *
 * The Navigator backend ships ~52 in-process tools. Most are read-only
 * shape lookups whose JSON output isn't worth more than a fallback
 * dump, but the nine high-signal Phase 6 tools (Inbox / Plays /
 * Coverage / Runs / Automations) deserve rich action cards with
 * deeplink chips so the user can click straight into the surface the
 * agent just touched instead of re-typing a URL.
 *
 * Architecture
 * ------------
 *
 * The chat client (`single-window-chat.tsx`) calls
 * :func:`renderToolResult` once per resolved tool call. We:
 *
 *   1. If the tool errored (``ok === false``), render :class:`ErrorCard`
 *      against ``result.error`` / ``result.message``.
 *   2. Else look up `TOOL_RENDERERS[toolName]`. If a renderer exists,
 *      we invoke it with the parsed JSON body. If the body itself
 *      carries an ``error`` field (defensive — backend can return
 *      ``{ ok: true, result: { error: ... } }`` if the tool returns
 *      ``{"error": "..."}`` without raising), we still hand off to
 *      :class:`ErrorCard`.
 *   3. Else fall back to :class:`JsonFallback` so unknown tools still
 *      render something useful.
 *
 * Every renderer is **defensive**: tool results may be ``null``,
 * partially shaped, or missing fields. We never throw — at worst we
 * return the JSON fallback. This protects the chat UX from any
 * server-side regression.
 *
 * Styling notes
 * -------------
 *
 * - Mirrors existing console patterns (rounded-2xl borders, white/[0.04]
 *   bg, aqua accents) — see ``inbox-item-row.tsx`` and ``run-row.tsx``.
 * - No new design tokens, no new npm packages.
 * - We avoid icons since `lucide-react` isn't installed; the rest of
 *   the codebase uses inline unicode glyphs (`↗`, `→`, `◐`, `▸`, …)
 *   so we follow the same convention.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { Badge, type BadgeTone } from "@/components/ui";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export type ToolResult = unknown;
export type ToolRenderer = (result: ToolResult) => ReactNode;

/**
 * Convenience entry point used by the chat client. Encapsulates the
 * "renderer lookup → error short-circuit → fallback" decision tree
 * so callers don't have to duplicate it.
 */
export function renderToolResult(
  toolName: string,
  result: ToolResult,
): ReactNode {
  // Surface server-shape errors uniformly first. Tools may return
  // ``{ "error": "...", "message": "..." }`` either through the
  // ``ok=false`` SSE branch (already unwrapped by ``normalizeToolResult``)
  // or as a top-level field on a successful payload (legacy shape).
  if (isErrorShape(result)) {
    return (
      <ErrorCard
        toolName={toolName}
        error={String(result.error)}
        message={
          typeof result.message === "string" ? result.message : undefined
        }
      />
    );
  }
  const renderer = TOOL_RENDERERS[toolName];
  if (renderer) {
    try {
      return renderer(result);
    } catch {
      // A buggy renderer should never blank the chat — degrade to
      // the JSON fallback so the user still sees something.
      return <JsonFallback toolName={toolName} result={result} />;
    }
  }
  return <JsonFallback toolName={toolName} result={result} />;
}

function isErrorShape(
  v: unknown,
): v is { error: unknown; message?: unknown } {
  return (
    typeof v === "object" &&
    v !== null &&
    !Array.isArray(v) &&
    "error" in (v as Record<string, unknown>) &&
    typeof (v as { error?: unknown }).error === "string"
  );
}

// ---------------------------------------------------------------------------
// Shared building blocks (chips, status pills, error cards, fallbacks)
// ---------------------------------------------------------------------------

/**
 * Deeplink chip — small rounded pill that opens a console route via
 * ``next/link``. Used as the "Open in …" CTA on every tool card.
 */
export function Chip({
  href,
  label,
  glyph,
  tone = "default",
}: {
  href: string;
  label: string;
  glyph?: string;
  tone?: "default" | "muted";
}) {
  const cls =
    tone === "muted"
      ? "border-white/15 bg-white/[0.04] text-white/70 hover:border-white/30 hover:text-white"
      : "border-aqua/40 bg-aqua/10 text-aqua hover:border-aqua/70 hover:bg-aqua/20";
  return (
    <Link
      href={href}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold transition",
        cls,
      )}
    >
      {glyph ? (
        <span aria-hidden className="text-[10px] opacity-80">
          {glyph}
        </span>
      ) : null}
      <span>{label}</span>
    </Link>
  );
}

const STATUS_TONE_MAP: Record<string, BadgeTone> = {
  // inbox
  new: "info",
  open: "info",
  snoozed: "warn",
  resolved: "ok",
  dismissed: "neutral",
  // runs / pipelines
  running: "info",
  queued: "info",
  succeeded: "ok",
  ok: "ok",
  failed: "err",
  fail: "err",
  cancelled: "neutral",
};

/** Small status pill keyed off backend status strings. */
export function StatusChip({ status }: { status: string }) {
  const tone = STATUS_TONE_MAP[status.toLowerCase()] ?? "neutral";
  return (
    <Badge tone={tone} dot={tone === "info"}>
      {status}
    </Badge>
  );
}

const TYPE_TONE_MAP: Record<string, BadgeTone> = {
  clarification: "info",
  improvement: "ok",
  failure: "err",
  approval: "warn",
  exception: "err",
};

function TypeChip({ type }: { type: string }) {
  const tone = TYPE_TONE_MAP[type.toLowerCase()] ?? "neutral";
  return <Badge tone={tone}>{type}</Badge>;
}

/**
 * Uniform error card. Special-cases ``forbidden`` with an
 * amber-bordered "Action requires admin" treatment so admin-gated
 * mutating tools (Wave B) read consistently across the chat. Other
 * error codes get a coral border. Mirrors the warning-strip style
 * already used in the inbox routing page so design review only has
 * one tone family to audit.
 */
export function ErrorCard({
  error,
  message,
  toolName,
}: {
  error: string;
  message?: string;
  toolName?: string;
}) {
  const isForbidden = error === "forbidden";
  const isPrecondition =
    error === "precondition_failed" ||
    error === "no_automation" ||
    error === "rate_limited";
  const cls = isForbidden
    ? "border-sun/40 bg-sun/[0.06] text-sun/95"
    : isPrecondition
      ? "border-amber-400/40 bg-amber-400/[0.06] text-amber-200/95"
      : "border-coral/40 bg-coral/[0.06] text-coral/95";
  const headline = isForbidden
    ? "Action requires admin"
    : friendlyErrorHeadline(error);
  return (
    <div
      className={cn(
        "rounded-2xl border px-4 py-3 text-[12px]",
        cls,
      )}
    >
      <div className="flex items-center gap-2">
        <span aria-hidden className="text-[12px]">⚠</span>
        <span className="font-semibold">{headline}</span>
        {toolName ? (
          <code className="ml-auto font-mono text-[10px] opacity-60">
            {toolName}
          </code>
        ) : null}
      </div>
      {message ? (
        <div className="mt-1 text-[12px] opacity-90">{message}</div>
      ) : null}
    </div>
  );
}

function friendlyErrorHeadline(error: string): string {
  switch (error) {
    case "not_found":
      return "Not found";
    case "validation_failed":
    case "bad_input":
      return "Invalid input";
    case "precondition_failed":
      return "Precondition failed";
    case "no_automation":
      return "Play not yet automated";
    case "rate_limited":
      return "Rate limited";
    case "conflict":
      return "Conflict";
    case "internal":
      return "Server error";
    default:
      return `Error · ${error}`;
  }
}

/**
 * Generic JSON dump for tools without a dedicated renderer. Looks
 * like a small expandable-ish card so the user has a hint that
 * something happened, with the raw payload shown if it's small.
 */
export function JsonFallback({
  toolName,
  result,
}: {
  toolName: string;
  result: ToolResult;
}) {
  let body: string;
  try {
    body = JSON.stringify(result, null, 2);
  } catch {
    body = String(result);
  }
  if (body.length > 1200) body = body.slice(0, 1200) + "\n…";
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <div className="mb-2 flex items-center gap-2">
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full bg-white/45"
        />
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/55">
          {prettyTool(toolName)}
        </span>
      </div>
      <pre className="max-h-72 overflow-auto rounded-md bg-black/40 p-2.5 text-[11px] leading-relaxed text-white/80">
        {body}
      </pre>
    </div>
  );
}

function prettyTool(name: string): string {
  return name.replace(/_/g, " ");
}

/**
 * Tool card outer chrome — a subtle bordered container with a small
 * "tool · name" header so the user can tell at a glance which tool
 * the result came from.
 */
function ToolCard({
  toolName,
  children,
  tone = "neutral",
}: {
  toolName: string;
  children: ReactNode;
  tone?: "neutral" | "success" | "preview";
}) {
  const cls =
    tone === "success"
      ? "border-emerald-400/30 bg-emerald-400/[0.04]"
      : tone === "preview"
        ? "border-sun/40 bg-sun/[0.05]"
        : "border-white/10 bg-white/[0.03]";
  return (
    <div className={cn("rounded-2xl border px-4 py-3", cls)}>
      <div className="mb-2 flex items-center gap-2">
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full bg-white/45"
        />
        <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/55">
          {prettyTool(toolName)}
        </span>
        {tone === "preview" ? (
          <span className="rounded-full bg-sun/25 px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-widest text-sun">
            preview · not applied
          </span>
        ) : null}
      </div>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers used by multiple renderers
// ---------------------------------------------------------------------------

function asArray<T = unknown>(v: unknown): T[] {
  return Array.isArray(v) ? (v as T[]) : [];
}

function asString(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

function asNumber(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function asObject(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function pluralize(n: number, one: string, many?: string): string {
  return n === 1 ? `${n} ${one}` : `${n} ${many ?? `${one}s`}`;
}

function relativeAge(iso: string | null): string {
  if (!iso) return "";
  const ts = new Date(iso).getTime();
  if (!Number.isFinite(ts)) return "";
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.round(hr / 24);
  return `${days}d ago`;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1).trimEnd() + "…";
}

function MetaSep() {
  return <span aria-hidden className="text-white/25">·</span>;
}

/** Inline horizontal coverage bar — mirrors `coverage-progress-bar.tsx`. */
function CoverageBar({
  pct,
  critical,
}: {
  pct: number;
  critical?: boolean;
}) {
  const clamped = Math.max(0, Math.min(1, pct));
  const danger = critical && clamped < 1;
  const fillCls = danger
    ? "bg-coral"
    : clamped >= 1
      ? "bg-emerald-400"
      : "bg-aqua";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/10">
      <div
        className={cn("h-1.5 rounded-full", fillCls)}
        style={{ width: `${Math.round(clamped * 100)}%` }}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// 1) inbox_list
// ---------------------------------------------------------------------------

type InboxListItem = {
  id: string;
  type: string;
  status: string;
  title: string;
  owner_display: string | null;
  repo_name: string | null;
  play_key: string | null;
  created_at: string | null;
};

function RenderInboxList(result: ToolResult): ReactNode {
  const obj = asObject(result);
  const items = asArray<Record<string, unknown>>(obj?.items).map(
    (raw): InboxListItem => ({
      id: asString(raw.id) ?? "",
      type: asString(raw.type) ?? "unknown",
      status: asString(raw.status) ?? "unknown",
      title: asString(raw.title) ?? "(untitled)",
      owner_display: asString(raw.owner_display),
      repo_name: asString(raw.repo_name),
      play_key: asString(raw.play_key),
      created_at: asString(raw.created_at),
    }),
  );
  const total = asNumber(obj?.total_estimate);

  if (items.length === 0) {
    return (
      <ToolCard toolName="inbox_list">
        <div className="text-[12px] text-white/65">
          No inbox items match — your queue is clear.
        </div>
        <div className="mt-2">
          <Chip href="/inbox" label="Open Inbox" glyph="↗" />
        </div>
      </ToolCard>
    );
  }

  return (
    <ToolCard toolName="inbox_list">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[12px] text-white/70">
          {pluralize(items.length, "item")}
          {total != null && total > items.length
            ? ` of ~${total}`
            : ""}
        </span>
        <Chip href="/inbox" label="Open Inbox" glyph="↗" />
      </div>
      <ul className="divide-y divide-white/5">
        {items.map((it) => (
          <li
            key={it.id || it.title}
            className="flex flex-wrap items-center gap-2 py-2 text-[12px]"
          >
            <TypeChip type={it.type} />
            <StatusChip status={it.status} />
            <span className="min-w-0 flex-1 truncate text-white/90">
              {it.title}
            </span>
            <div className="flex items-center gap-2 text-[11px] text-white/55">
              {it.owner_display ? <span>{it.owner_display}</span> : null}
              {it.created_at ? (
                <>
                  {it.owner_display ? <MetaSep /> : null}
                  <span title={it.created_at}>
                    {relativeAge(it.created_at)}
                  </span>
                </>
              ) : null}
              {it.id ? (
                <Chip
                  href={`/inbox/${it.id}`}
                  label="Open"
                  glyph="→"
                  tone="muted"
                />
              ) : null}
            </div>
          </li>
        ))}
      </ul>
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 2) inbox_get
// ---------------------------------------------------------------------------

type InboxEvent = {
  action: string;
  actor_kind: string | null;
  created_at: string | null;
};

function RenderInboxGet(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj) return <JsonFallback toolName="inbox_get" result={result} />;
  const id = asString(obj.id);
  const title = asString(obj.title) ?? "(untitled item)";
  const status = asString(obj.status) ?? "unknown";
  const type = asString(obj.type) ?? "unknown";
  const ownerDisplay = asString(obj.owner_display);
  const summary = asString(obj.summary);
  const repoName = asString(obj.repo_name);
  const playKey = asString(obj.play_key);
  const events = asArray<Record<string, unknown>>(obj.events)
    .slice(-3)
    .map(
      (e): InboxEvent => ({
        action: asString(e.action) ?? "event",
        actor_kind: asString(e.actor_kind),
        created_at: asString(e.created_at),
      }),
    );

  return (
    <ToolCard toolName="inbox_get">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <TypeChip type={type} />
            <StatusChip status={status} />
            {repoName ? (
              <code className="font-mono text-[11px] text-white/55">
                {repoName}
              </code>
            ) : null}
          </div>
          <div className="mt-1.5 text-sm font-semibold text-white">
            {title}
          </div>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-white/55">
            {ownerDisplay ? <span>owner · {ownerDisplay}</span> : null}
            {playKey ? (
              <>
                {ownerDisplay ? <MetaSep /> : null}
                <code className="font-mono">{playKey}</code>
              </>
            ) : null}
          </div>
        </div>
        {id ? <Chip href={`/inbox/${id}`} label="Open in Inbox" glyph="↗" /> : null}
      </div>

      {summary ? (
        <p className="mt-3 text-[12px] leading-relaxed text-white/80">
          {truncate(summary, 320)}
        </p>
      ) : null}

      {events.length > 0 ? (
        <div className="mt-3 border-t border-white/5 pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
            Recent activity
          </div>
          <ul className="space-y-0.5 text-[11px] text-white/65">
            {events.map((ev, i) => (
              <li key={i} className="flex items-center gap-2">
                <span aria-hidden className="text-white/35">·</span>
                <span className="text-white/85">{ev.action}</span>
                {ev.actor_kind ? (
                  <span className="text-white/45">by {ev.actor_kind}</span>
                ) : null}
                {ev.created_at ? (
                  <span className="text-white/35" title={ev.created_at}>
                    {relativeAge(ev.created_at)}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 3) inbox_dispose
// ---------------------------------------------------------------------------

function RenderInboxDispose(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj) return <JsonFallback toolName="inbox_dispose" result={result} />;

  const dryRun = obj.dry_run === true;
  const payload = (
    dryRun
      ? asObject(obj.would_apply)
      : obj
  ) as Record<string, unknown> | null;
  if (!payload) {
    return <JsonFallback toolName="inbox_dispose" result={result} />;
  }

  const itemId = asString(payload.inbox_item_id);
  const newStatus = asString(payload.new_status) ?? "—";
  const disposition = asString(payload.applied_disposition) ?? "—";
  const resolution = asString(payload.resolution);
  const sideEffects = dryRun
    ? // dry_run packs side_effects as a string summary
      asString(payload.side_effects)
        ? [{ kind: String(payload.side_effects), count: 0 }]
        : []
    : asArray<Record<string, unknown>>(payload.side_effects).map((se) => ({
        kind: asString(se.kind) ?? "side_effect",
        count: asNumber(se.count) ?? 0,
      }));

  return (
    <ToolCard
      toolName="inbox_dispose"
      tone={dryRun ? "preview" : "success"}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-white">
            {dryRun ? "Would apply" : "Applied"} ·{" "}
            <span className="text-aqua">{disposition}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-white/65">
            <span>new status</span>
            <StatusChip status={newStatus} />
            {resolution ? (
              <>
                <MetaSep />
                <span>resolution · {resolution}</span>
              </>
            ) : null}
          </div>
        </div>
        {itemId ? (
          <Chip href={`/inbox/${itemId}`} label="Open in Inbox" glyph="↗" />
        ) : null}
      </div>

      {sideEffects.length > 0 ? (
        <div className="mt-3 border-t border-white/5 pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
            Side effects
          </div>
          <ul className="space-y-0.5 text-[11px] text-white/75">
            {sideEffects.map((se, i) => (
              <li key={i} className="flex items-center gap-2">
                <span aria-hidden className="text-white/35">·</span>
                <span className="text-white/90">{se.kind}</span>
                {se.count > 0 ? (
                  <span className="text-white/55">× {se.count}</span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 4) plays_coverage
// ---------------------------------------------------------------------------

type CoverageRow = {
  play_key: string;
  play_title: string;
  category: string | null;
  critical: boolean;
  coverage_pct: number;
  repos_uncovered_count: number;
  repos_covered_count: number;
};

function RenderPlaysCoverage(result: ToolResult): ReactNode {
  const obj = asObject(result);
  const rows = asArray<Record<string, unknown>>(obj?.rows).map(
    (r): CoverageRow => ({
      play_key: asString(r.play_key) ?? "",
      play_title: asString(r.play_title) ?? asString(r.play_key) ?? "(unknown play)",
      category: asString(r.category),
      critical: r.critical === true,
      coverage_pct: asNumber(r.coverage_pct) ?? 0,
      repos_uncovered_count: asNumber(r.repos_uncovered_count) ?? 0,
      repos_covered_count: asNumber(r.repos_covered_count) ?? 0,
    }),
  );

  if (rows.length === 0) {
    return (
      <ToolCard toolName="plays_coverage">
        <div className="text-[12px] text-white/65">
          No coverage data — no plays match the filter.
        </div>
        <div className="mt-2">
          <Chip
            href="/automations?tab=coverage"
            label="Open Coverage"
            glyph="↗"
          />
        </div>
      </ToolCard>
    );
  }

  return (
    <ToolCard toolName="plays_coverage">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[12px] text-white/70">
          {pluralize(rows.length, "play")}
        </span>
        <Chip
          href="/automations?tab=coverage"
          label="Open Coverage"
          glyph="↗"
        />
      </div>
      <ul className="space-y-2">
        {rows.map((row) => {
          const pct = Math.round(
            Math.max(0, Math.min(1, row.coverage_pct)) * 100,
          );
          const isGap =
            row.critical && row.repos_uncovered_count > 0;
          return (
            <li
              key={row.play_key}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
            >
              <div className="flex items-center gap-2">
                {row.critical ? (
                  <Badge tone="err" dot>
                    Critical
                  </Badge>
                ) : null}
                <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-white">
                  {row.play_title}
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-white/65">
                  {pct}%
                </span>
                {row.repos_uncovered_count > 0 ? (
                  <Badge tone={isGap ? "err" : "neutral"}>
                    {row.repos_uncovered_count} uncovered
                  </Badge>
                ) : (
                  <Badge tone="ok">covered</Badge>
                )}
                <Chip
                  href={`/automations?tab=coverage&play=${encodeURIComponent(
                    row.play_key,
                  )}`}
                  label="Open"
                  glyph="→"
                  tone="muted"
                />
              </div>
              <div className="mt-2">
                <CoverageBar
                  pct={row.coverage_pct}
                  critical={row.critical}
                />
              </div>
            </li>
          );
        })}
      </ul>
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 5) plays_get
// ---------------------------------------------------------------------------

function RenderPlaysGet(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj) return <JsonFallback toolName="plays_get" result={result} />;

  const playKey = asString(obj.play_key) ?? "";
  const title = asString(obj.title) ?? playKey ?? "(unknown play)";
  const category = asString(obj.category);
  const critical = obj.critical === true;
  const summary = asString(obj.summary);
  const includes = asArray<unknown>(obj.includes)
    .map((i) => (typeof i === "string" ? i : null))
    .filter((i): i is string => i !== null);

  return (
    <ToolCard toolName="plays_get">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {category ? <Badge tone="neutral">{category}</Badge> : null}
            {critical ? (
              <Badge tone="err" dot>
                Critical
              </Badge>
            ) : null}
          </div>
          <div className="mt-1.5 text-sm font-semibold text-white">
            {title}
          </div>
          {playKey ? (
            <code className="mt-0.5 block font-mono text-[10px] text-white/40">
              {playKey}
            </code>
          ) : null}
        </div>
        {playKey ? (
          <Chip
            href={`/plays?play=${encodeURIComponent(playKey)}`}
            label="Open Play"
            glyph="↗"
          />
        ) : null}
      </div>

      {summary ? (
        <p className="mt-3 text-[12px] leading-relaxed text-white/80">
          {truncate(summary, 320)}
        </p>
      ) : null}

      {includes.length > 0 ? (
        <div className="mt-3 border-t border-white/5 pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
            Includes
          </div>
          <div className="flex flex-wrap gap-1.5">
            {includes.slice(0, 12).map((inc) => (
              <code
                key={inc}
                className="rounded-full bg-white/[0.06] px-2 py-0.5 font-mono text-[10px] text-white/75"
              >
                {inc}
              </code>
            ))}
            {includes.length > 12 ? (
              <span className="text-[10px] text-white/45">
                +{includes.length - 12} more
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 6) runs_query
// ---------------------------------------------------------------------------

type RunRow = {
  id: string;
  status: string;
  outcome_text: string | null;
  play_key: string | null;
  repo_name: string | null;
  findings_count: number | null;
  escalations_count: number;
  started_at: string | null;
};

function RenderRunsQuery(result: ToolResult): ReactNode {
  const obj = asObject(result);
  const runs = asArray<Record<string, unknown>>(obj?.runs).map(
    (r): RunRow => ({
      id: asString(r.id) ?? "",
      status: asString(r.status) ?? "unknown",
      outcome_text: asString(r.outcome_text),
      play_key: asString(r.play_key),
      repo_name: asString(r.repo_name),
      findings_count: asNumber(r.findings_count),
      escalations_count: asNumber(r.escalations_count) ?? 0,
      started_at: asString(r.started_at),
    }),
  );

  if (runs.length === 0) {
    return (
      <ToolCard toolName="runs_query">
        <div className="text-[12px] text-white/65">
          No runs match — try widening the filter.
        </div>
        <div className="mt-2">
          <Chip href="/runs" label="Open Runs" glyph="↗" />
        </div>
      </ToolCard>
    );
  }

  return (
    <ToolCard toolName="runs_query">
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[12px] text-white/70">
          {pluralize(runs.length, "run")}
        </span>
        <Chip href="/runs" label="Open Runs" glyph="↗" />
      </div>
      <ul className="space-y-2">
        {runs.map((run) => {
          const headline =
            run.outcome_text ?? `Run ${run.status}`;
          return (
            <li
              key={run.id || headline}
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="min-w-0 flex-1 text-[13px] font-semibold leading-snug text-white">
                  {headline}
                </span>
                <StatusChip status={run.status} />
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-white/55">
                {run.repo_name ? (
                  <code className="font-mono text-white/65">
                    {run.repo_name}
                  </code>
                ) : null}
                {run.play_key ? (
                  <>
                    {run.repo_name ? <MetaSep /> : null}
                    <code className="font-mono">{run.play_key}</code>
                  </>
                ) : null}
                {run.findings_count != null && run.findings_count > 0 ? (
                  <>
                    <MetaSep />
                    <span>
                      {pluralize(run.findings_count, "finding")}
                    </span>
                  </>
                ) : null}
                {run.escalations_count > 0 ? (
                  <Badge tone="err">
                    {pluralize(run.escalations_count, "escalation")}
                  </Badge>
                ) : null}
                {run.started_at ? (
                  <>
                    <MetaSep />
                    <span title={run.started_at}>
                      {relativeAge(run.started_at)}
                    </span>
                  </>
                ) : null}
                {run.id ? (
                  <Chip
                    href={`/runs/${run.id}`}
                    label="Open Run"
                    glyph="→"
                    tone="muted"
                  />
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 7) run_detail
// ---------------------------------------------------------------------------

function RenderRunDetail(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj) return <JsonFallback toolName="run_detail" result={result} />;

  const id = asString(obj.id);
  const status = asString(obj.status) ?? "unknown";
  const playKey = asString(obj.play_key);
  const repoName = asString(obj.repo_name);
  const outcome = asObject(obj.outcome) ?? {};
  const outcomeText =
    asString(outcome.outcome_text) ?? asString(obj.summary);
  const findingsBySeverity = asObject(outcome.findings_by_severity) ?? {};
  const escalations = asArray<Record<string, unknown>>(obj.escalations).map(
    (e) => ({
      inbox_item_id: asString(e.inbox_item_id),
      reason: asString(e.escalation_reason),
      title: asString(e.item_title),
      status: asString(e.item_status),
      type: asString(e.item_type),
    }),
  );

  const sevs: Array<["critical" | "high" | "medium" | "low", number]> = (
    ["critical", "high", "medium", "low"] as const
  )
    .map((k) => [k, asNumber(findingsBySeverity[k]) ?? 0] as const)
    .filter(([, n]) => n > 0)
    .map(([k, n]) => [k, n] as ["critical" | "high" | "medium" | "low", number]);
  const sevToneMap: Record<string, BadgeTone> = {
    critical: "err",
    high: "err",
    medium: "warn",
    low: "info",
  };

  return (
    <ToolCard toolName="run_detail">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusChip status={status} />
            {repoName ? (
              <code className="font-mono text-[11px] text-white/55">
                {repoName}
              </code>
            ) : null}
            {playKey ? (
              <>
                <MetaSep />
                <code className="font-mono text-[11px] text-white/55">
                  {playKey}
                </code>
              </>
            ) : null}
          </div>
          {outcomeText ? (
            <div className="mt-1.5 text-sm font-semibold leading-snug text-white">
              {outcomeText}
            </div>
          ) : null}
        </div>
        {id ? <Chip href={`/runs/${id}`} label="Open Run" glyph="↗" /> : null}
      </div>

      {sevs.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px]">
          <span className="text-white/55">findings ·</span>
          {sevs.map(([k, n]) => (
            <Badge key={k} tone={sevToneMap[k]}>
              {n} {k}
            </Badge>
          ))}
        </div>
      ) : null}

      {escalations.length > 0 ? (
        <div className="mt-3 border-t border-white/5 pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.18em] text-white/45">
            Escalations
          </div>
          <ul className="space-y-1">
            {escalations.map((esc, i) => (
              <li
                key={esc.inbox_item_id || i}
                className="flex flex-wrap items-center gap-2 text-[11px] text-white/75"
              >
                <span aria-hidden className="text-coral">⚠</span>
                <span className="min-w-0 flex-1 truncate">
                  {esc.title ?? esc.reason ?? "Escalated"}
                </span>
                {esc.status ? <StatusChip status={esc.status} /> : null}
                {esc.inbox_item_id ? (
                  <Chip
                    href={`/inbox/${esc.inbox_item_id}`}
                    label="Open in Inbox"
                    glyph="→"
                    tone="muted"
                  />
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 8) play_run_now
// ---------------------------------------------------------------------------

function RenderPlayRunNow(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj) return <JsonFallback toolName="play_run_now" result={result} />;

  const runId = asString(obj.run_id);
  const playKey = asString(obj.play_key) ?? "play";
  const repoId = asString(obj.repo_id);
  const status = asString(obj.status) ?? "queued";

  return (
    <ToolCard toolName="play_run_now" tone="success">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-white">
            Queued{runId ? ` — run #${runId.slice(0, 8)}` : ""} for{" "}
            <code className="font-mono text-[12px] text-aqua">{playKey}</code>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-white/65">
            <StatusChip status={status} />
            {repoId ? (
              <>
                <MetaSep />
                <span>
                  repo ·{" "}
                  <code className="font-mono text-white/55">{repoId}</code>
                </span>
              </>
            ) : null}
          </div>
        </div>
        {runId ? (
          <Chip href={`/runs/${runId}`} label="Open Run" glyph="↗" />
        ) : null}
      </div>
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// 9) automation_toggle
// ---------------------------------------------------------------------------

function RenderAutomationToggle(result: ToolResult): ReactNode {
  const obj = asObject(result);
  if (!obj)
    return <JsonFallback toolName="automation_toggle" result={result} />;

  const pipelineId = asString(obj.pipeline_id);
  const enabled = obj.enabled === true;
  const priorEnabled = obj.prior_enabled === true;
  const noChange = enabled === priorEnabled;

  return (
    <ToolCard toolName="automation_toggle" tone="success">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-white">
            Automation is now{" "}
            <span className={enabled ? "text-emerald-300" : "text-white/55"}>
              {enabled ? "enabled" : "disabled"}
            </span>
          </div>
          <div className="mt-1 text-[11px] text-white/55">
            {noChange
              ? "No state change — already in this state."
              : `Was ${priorEnabled ? "enabled" : "disabled"}.`}
            {pipelineId ? (
              <>
                {" · "}
                <code className="font-mono text-white/55">{pipelineId}</code>
              </>
            ) : null}
          </div>
        </div>
        {pipelineId ? (
          <Chip
            href={`/automations?id=${encodeURIComponent(pipelineId)}`}
            label="Open Automation"
            glyph="↗"
          />
        ) : null}
      </div>
    </ToolCard>
  );
}

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

export const TOOL_RENDERERS: Record<string, ToolRenderer> = {
  inbox_list: RenderInboxList,
  inbox_get: RenderInboxGet,
  inbox_dispose: RenderInboxDispose,
  plays_coverage: RenderPlaysCoverage,
  plays_get: RenderPlaysGet,
  runs_query: RenderRunsQuery,
  run_detail: RenderRunDetail,
  play_run_now: RenderPlayRunNow,
  automation_toggle: RenderAutomationToggle,
};
