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
 *
 * The visible summary always stays compact (dot · tool · short
 * error). Long error blobs, multi-line stacks, or anything bigger
 * than ~200 chars get tucked behind a ``<details>`` so the chat
 * doesn't get bricked by a tool dumping a JSON payload at us.
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
  const dotCls = isForbidden
    ? "bg-sun"
    : isPrecondition
      ? "bg-amber-300"
      : "bg-coral";
  const headline = isForbidden
    ? "Action requires admin"
    : friendlyErrorHeadline(error);
  const fullText = (message && message.length > 0 ? message : error) ?? "";
  const firstLine = fullText.split(/\r?\n/, 1)[0] ?? "";
  const shortMessage =
    firstLine.length > 120 ? firstLine.slice(0, 119).trimEnd() + "…" : firstLine;
  // Stash the raw payload behind <details> when it's long, multi-
  // line, or differs from what we're showing inline so the user
  // can still inspect the underlying error if they need to.
  const isMultiline = /\r?\n/.test(fullText);
  const needsDetails =
    fullText.length > 200 || isMultiline || fullText.length > shortMessage.length;
  return (
    <div
      className={cn(
        "rounded-2xl border px-4 py-3 text-[12px]",
        cls,
      )}
    >
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className={cn("h-1.5 w-1.5 shrink-0 rounded-full", dotCls)}
        />
        {toolName ? (
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] opacity-70">
            {prettyTool(toolName)}
          </span>
        ) : null}
        <span className="font-semibold">{headline}</span>
      </div>
      {shortMessage ? (
        <div className="mt-1 text-[12px] opacity-90">{shortMessage}</div>
      ) : null}
      {needsDetails ? (
        <details className="group mt-2">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[11px] opacity-70 hover:opacity-100">
            <span
              aria-hidden
              className="inline-block transition-transform group-open:rotate-90"
            >
              ▸
            </span>
            <span>show full error</span>
          </summary>
          <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap rounded-md bg-black/40 p-2.5 text-[11px] leading-relaxed">
            {fullText}
          </pre>
        </details>
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
 * Generic JSON dump for tools without a dedicated renderer.
 *
 * Default-collapsed: the chat stays clean for the 80% of tool
 * calls that don't need a rich card. The summary line gives a
 * one-glance outcome blurb derived cheaply from the result shape
 * (array length, ``ok`` flag, top-level ``count``/``total``, etc.)
 * and for known tool families we lean on
 * :func:`businessFriendlyBlurb` for a slightly nicer phrasing.
 *
 * Power users click the chevron to see the raw JSON. We still
 * truncate the dumped body at ~1200 chars so a runaway tool
 * doesn't tank the scroll.
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
  const blurb = jsonFallbackBlurb(toolName, result);
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2">
          <span
            aria-hidden
            className="h-1.5 w-1.5 shrink-0 rounded-full bg-white/45"
          />
          <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-white/55">
            {prettyTool(toolName)}
          </span>
          <span className="min-w-0 flex-1 truncate text-[12px] text-white/65">
            {blurb}
          </span>
          <span
            aria-hidden
            className="inline-block text-[10px] text-white/40 transition-transform group-open:rotate-90"
          >
            ▸
          </span>
        </summary>
        <pre className="mt-2 max-h-72 overflow-auto rounded-md bg-black/40 p-2.5 text-[11px] leading-relaxed text-white/80">
          {body}
        </pre>
      </details>
    </div>
  );
}

/**
 * One-line outcome blurb shown in the collapsed ``JsonFallback``
 * summary. Best-effort — we look at common payload shapes (arrays,
 * ``ok`` flags, ``count``/``total`` keys) and fall back to a
 * generic "result hidden" message. Domain-specific phrasings live
 * in :func:`businessFriendlyBlurb` so growing the list later is
 * a one-place edit.
 */
function jsonFallbackBlurb(toolName: string, result: ToolResult): string {
  const friendly = businessFriendlyBlurb(toolName, result);
  if (friendly) return friendly;
  if (Array.isArray(result)) {
    return `${result.length} ${result.length === 1 ? "result" : "results"}`;
  }
  const obj = asObject(result);
  if (obj) {
    if (obj.ok === true) return "ok";
    if (obj.ok === false) return "failed";
    const total = asNumber(obj.total) ?? asNumber(obj.total_estimate);
    const count = asNumber(obj.count);
    if (count != null && total != null && total > count) {
      return `${count} of ~${total}`;
    }
    if (count != null) return `count: ${count}`;
    if (total != null) return `total: ${total}`;
    for (const key of ["items", "rows", "results", "runs", "data"]) {
      const arr = obj[key];
      if (Array.isArray(arr)) {
        return `${arr.length} ${arr.length === 1 ? "result" : "results"}`;
      }
    }
  }
  return "result hidden — click to expand";
}

/**
 * Domain-aware blurbs for the collapsed JSON fallback summary.
 * Returns ``null`` when nothing better than the generic shape
 * heuristic is possible — :func:`jsonFallbackBlurb` will handle
 * the fallback. Keep these cheap and defensive: tool payloads
 * may be partially shaped in production.
 */
function businessFriendlyBlurb(
  toolName: string,
  result: ToolResult,
): string | null {
  const obj = asObject(result);
  if (!obj) return null;
  if (toolName === "inbox_counts" || toolName.startsWith("inbox_count")) {
    const open =
      asNumber(obj.open) ?? asNumber(obj.new) ?? asNumber(obj.active);
    const waiting =
      asNumber(obj.waiting) ?? asNumber(obj.snoozed) ?? asNumber(obj.pending);
    const parts: string[] = [];
    if (open != null) parts.push(`open: ${open}`);
    if (waiting != null) parts.push(`waiting: ${waiting}`);
    if (parts.length > 0) return parts.join(" · ");
  }
  if (toolName.startsWith("runs_") || toolName === "run_detail") {
    const runs = obj.runs;
    if (Array.isArray(runs)) {
      return `${runs.length} ${runs.length === 1 ? "run" : "runs"}`;
    }
    const status = asString(obj.status);
    if (status) return `status: ${status}`;
  }
  if (toolName.startsWith("repo_")) {
    const repos = obj.repos ?? obj.items;
    if (Array.isArray(repos)) {
      return `${repos.length} ${repos.length === 1 ? "repo" : "repos"}`;
    }
    const name = asString(obj.repo_name) ?? asString(obj.name);
    if (name) return name;
  }
  return null;
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
          <Chip href="/process" label="Open Runs" glyph="↗" />
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
        <Chip href="/process" label="Open Runs" glyph="↗" />
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
                    href={`/process`}
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
        {id ? <Chip href={`/process`} label="Open Run" glyph="↗" /> : null}
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
// Registry
// ---------------------------------------------------------------------------

export const TOOL_RENDERERS: Record<string, ToolRenderer> = {
  inbox_list: RenderInboxList,
  inbox_get: RenderInboxGet,
  inbox_dispose: RenderInboxDispose,
  runs_query: RenderRunsQuery,
  run_detail: RenderRunDetail,
};

// ---------------------------------------------------------------------------
// Friendly status-line verbs (A5)
// ---------------------------------------------------------------------------

/**
 * Human-readable phrasing for the in-flight status line that
 * appears beneath the chat while a tool is running. Keyed by raw
 * backend tool name so the chat client and the renderer registry
 * stay in sync (no risk of drifting verbs between files).
 *
 * Unknown tools fall back to :data:`TOOL_FRIENDLY_VERB_FALLBACK`
 * via :func:`friendlyToolVerb` so we never show jargon like
 * "Calling repo_intel_search…" to non-engineers.
 */
export const TOOL_FRIENDLY_VERB: Record<string, string> = {
  inbox_list: "Looking through your inbox…",
  inbox_get: "Pulling up that item…",
  inbox_counts: "Counting inbox items…",
  inbox_dispose: "Resolving the Inbox item…",
  runs_query: "Searching recent Runs…",
  run_detail: "Loading Run details…",
};

export const TOOL_FRIENDLY_VERB_FALLBACK = "Working on it…";

/**
 * Resolve the friendly verb for ``toolName``, falling back to a
 * generic "Working on it…" so non-engineer users never see raw
 * snake_case tool identifiers in the chat status line.
 */
export function friendlyToolVerb(toolName: string): string {
  return TOOL_FRIENDLY_VERB[toolName] ?? TOOL_FRIENDLY_VERB_FALLBACK;
}
