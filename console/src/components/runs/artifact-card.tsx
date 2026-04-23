/**
 * Reusable artifact card for the ``/runs/[id]`` detail surface
 * (RFC-0010 Wave 6 / Phase 3 ticket P3-05).
 *
 * Renders one entry from ``RunSummary.artifacts[]`` — a typed glyph,
 * the artifact title (truncated with a tooltip), an optional ref
 * summary derived from the URL host + path, and an "open" arrow
 * when the artifact has a ``ref`` we can link to. The whole card
 * becomes an external anchor when ``ref`` is set; otherwise it
 * renders as a static panel so empty / inline artifacts still slot
 * cleanly into the grid.
 *
 * Type-to-icon mapping mirrors the ticket spec:
 *   - ``pr``      → 🔀
 *   - ``issue``   → ⊙
 *   - ``comment`` → 💬
 *   - ``doc``     → 📄
 *   - anything else → ◇
 */

import type { RunSummaryArtifact } from "@/lib/api/client";

const TYPE_GLYPH: Record<string, string> = {
  pr: "\u{1F500}", // 🔀
  issue: "\u{29B5}", // ⊙
  comment: "\u{1F4AC}", // 💬
  doc: "\u{1F4C4}", // 📄
};

const FALLBACK_GLYPH = "\u{25C7}"; // ◇

const TYPE_LABEL: Record<string, string> = {
  pr: "Pull request",
  issue: "Issue",
  comment: "Comment",
  doc: "Doc",
};

function glyphFor(type: string): string {
  const key = type.toLowerCase();
  return TYPE_GLYPH[key] ?? FALLBACK_GLYPH;
}

function labelFor(type: string): string {
  const key = type.toLowerCase();
  return TYPE_LABEL[key] ?? type.toUpperCase();
}

/**
 * Best-effort short summary for the ``ref`` field. PR-style URLs
 * (``github.com/owner/repo/pull/42``) collapse to ``#42 ·
 * github.com/owner/repo``; everything else falls back to the
 * full URL string when not parseable.
 */
function summarizeRef(ref: string): string {
  try {
    const url = new URL(ref);
    const segments = url.pathname.split("/").filter(Boolean);
    // Look for ``/<owner>/<repo>/{pull,issues}/<num>``
    const numIdx = segments.findIndex(
      (s) => s === "pull" || s === "issues",
    );
    if (numIdx >= 0 && segments[numIdx + 1]) {
      const owner = segments[0];
      const repo = segments[1];
      return `#${segments[numIdx + 1]} \u00b7 ${url.host}/${owner}/${repo}`;
    }
    if (segments.length === 0) return url.host;
    return `${url.host}/${segments.slice(0, 2).join("/")}`;
  } catch {
    return ref;
  }
}

const TITLE_MAX = 80;

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return `${s.slice(0, n - 1).trimEnd()}\u2026`;
}

export function ArtifactCard({ artifact }: { artifact: RunSummaryArtifact }) {
  const glyph = glyphFor(artifact.type);
  const label = labelFor(artifact.type);
  const refSummary = artifact.ref ? summarizeRef(artifact.ref) : null;
  const titleFull = artifact.title;
  const titleTrunc = truncate(titleFull, TITLE_MAX);

  const inner = (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-white/10 bg-white/[0.04] text-base"
          >
            {glyph}
          </span>
          <span className="text-[10px] font-bold uppercase tracking-widest text-white/55">
            {label}
          </span>
        </div>
        {artifact.ref && (
          <span
            aria-hidden
            className="text-xs text-white/55 transition group-hover:text-aqua"
          >
            {"\u2197"}
          </span>
        )}
      </div>
      <p
        className="line-clamp-2 text-sm font-semibold text-white/90"
        title={titleFull.length > TITLE_MAX ? titleFull : undefined}
      >
        {titleTrunc}
      </p>
      {refSummary && (
        <p className="mt-auto truncate text-[11px] text-white/55">
          {refSummary}
        </p>
      )}
    </div>
  );

  const cls =
    "group block rounded-xl border border-white/10 bg-white/[0.03] p-3 transition";

  if (artifact.ref) {
    return (
      <a
        href={artifact.ref}
        target="_blank"
        rel="noopener noreferrer"
        className={`${cls} hover:border-aqua/40 hover:bg-white/[0.06]`}
      >
        {inner}
      </a>
    );
  }

  return <div className={cls}>{inner}</div>;
}
