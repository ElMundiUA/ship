"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";

import { Badge } from "@/components/ui";
import type { ApiCatalogPattern, ApiPatternInput } from "@/lib/api/client";

/**
 * Shared "Play" card component (RFC-0010 §2 / P1-10 + P1-11).
 *
 * Used by both ``/plays`` (the merged catalog) and ``/requests``
 * (the legacy one-shot grid that stays mounted until subagent D
 * lands the redirect). Replaces the inline ``PatternCard`` that
 * used to live in ``requests/requests-catalog.tsx``.
 *
 * **Display contract** (P1-10):
 *
 * - The pattern's business ``title`` (frontmatter ``name``) is the
 *   prominent line.
 * - A small subtitle line reads ``"Includes N reviews · runs in
 *   {mode}"`` where:
 *   - ``N`` = the count of ``outputs`` declared by the pattern. The
 *     current ``ApiCatalogPattern`` shape does **not** expose
 *     ``outputs`` (see ``console/src/lib/api/client.ts`` ::
 *     :class:`ApiCatalogPattern`), so callers either pass an
 *     explicit ``reviewsCount`` (sourced from somewhere else) or we
 *     fall back to ``3`` as a plausible default. P4-06 will wire the
 *     real value once the backend exposes it.
 *   - ``{mode}`` = ``"event-driven"`` | ``"on-demand"`` |
 *     ``"scheduled"``. Resolved from either the ``ApiCatalogPattern``
 *     ``modes`` array or the ``ApiLaneCatalogEntry`` event /
 *     schedule fields — see :func:`resolvePlayMode`.
 *
 * **CTA contract** (P1-11):
 *
 * - **Primary "Run now"** — opens the dispatch form inline. Reuses
 *   the same ``onSubmit`` flow ``requests-catalog.tsx`` already
 *   wires to ``POST /api/requests``. Hidden when ``pattern`` is
 *   absent (lane-only Plays don't dispatch one-shots).
 * - **Secondary "Automate"** — link to ``/automations/new?play=<id>``.
 *   That route doesn't exist yet (404 placeholder); sibling subagent
 *   C will plumb it. The link emits regardless so the contract is
 *   visible end-to-end.
 *
 * For LANE-only patterns (``modes: [lane]`` with no ``request``
 * mode, or entries that come straight from ``listLaneCatalog``) we
 * downgrade the primary CTA to "Automate" — there is no one-shot
 * dispatch path for them, and we don't want to render a dead "Run
 * now" button.
 */

export type PlayMode = "event-driven" | "on-demand" | "scheduled";

export type PlayCardCtaLayout = {
  /**
   * When true, render the "Run now" button as the primary CTA. The
   * card needs a non-null ``pattern`` and a non-null ``onSubmit`` to
   * actually dispatch — both are enforced by the consuming page's
   * data shape (see ``/plays`` and ``/requests``).
   */
  showRunNow: boolean;
  /**
   * When true, render the "Automate" link as a button. We always
   * emit this for catalog Plays so the contract with sibling
   * subagent C is visible; lane-only Plays surface it as the
   * primary CTA instead of secondary.
   */
  showAutomate: boolean;
};

export type CardState =
  | { mode: "idle" }
  | { mode: "open" }
  | { mode: "saving" }
  | { mode: "error"; message: string; code?: string };

/**
 * Resolve a Play's ``mode`` label from either of the two catalog
 * shapes the redesign sprint mixes on ``/plays``:
 *
 * - ``ApiCatalogPattern.modes`` includes ``"request"`` for one-shot
 *   patterns and ``"lane"`` for recurring/event-driven ones. The
 *   current pattern shape does not distinguish event vs schedule
 *   inside ``"lane"`` — callers that have richer info (e.g. a
 *   ``default_trigger`` cron / event slot) can pass an explicit
 *   ``mode`` override instead.
 * - ``ApiLaneCatalogEntry.event`` and ``.schedule`` map cleanly to
 *   ``event-driven`` / ``scheduled``.
 *
 * Fallback: ``"on-demand"``.
 */
export function resolvePlayMode(
  modes: string[] | undefined,
  hints?: { event?: string | null; schedule?: string | null },
): PlayMode {
  if (hints?.event) return "event-driven";
  if (hints?.schedule) return "scheduled";
  if (modes && modes.length > 0) {
    if (modes.includes("event")) return "event-driven";
    if (modes.includes("schedule")) return "scheduled";
    if (modes.includes("request")) return "on-demand";
    if (modes.includes("lane")) {
      // Pure lane mode without explicit event/schedule fields is
      // most often a scheduled recipe (pre-redesign default). We
      // can't tell from this shape, so degrade to "scheduled" as
      // the safer assumption — the actual cron lives in the lane
      // detail page, not the card.
      return "scheduled";
    }
  }
  return "on-demand";
}

/**
 * Format the subtitle line per P1-10. ``reviewsCount`` is the
 * declared output count; we default to ``3`` when undefined because
 * the current ``ApiCatalogPattern`` shape doesn't carry it (see
 * file-level docstring for the proper P4-06 follow-up).
 */
export function formatPlaySubtitle(
  reviewsCount: number | undefined,
  mode: PlayMode,
): string {
  const n = typeof reviewsCount === "number" ? reviewsCount : 3;
  return `Includes ${n} ${n === 1 ? "review" : "reviews"} · runs in ${mode}`;
}

export function PlayCard({
  id,
  title,
  description,
  tags,
  reviewsCount,
  mode,
  pattern,
  expanded,
  state,
  onToggle,
  onSubmit,
  ctaLayout,
}: {
  /** Pattern id used as ``?play=<id>`` on the Automate link. */
  id: string;
  title: string;
  description: string;
  tags?: string[];
  reviewsCount?: number;
  mode: PlayMode;
  /**
   * When present and ``ctaLayout.showRunNow`` is true the card can
   * dispatch a one-shot via the inline form (reuses the existing
   * PatternForm). Lane-only Plays leave this undefined.
   */
  pattern?: ApiCatalogPattern;
  expanded: boolean;
  state: CardState;
  onToggle: () => void;
  /** Required when ``pattern`` is provided and the user can dispatch. */
  onSubmit?: (inputs: Record<string, string>) => void;
  ctaLayout: PlayCardCtaLayout;
}) {
  const subtitle = formatPlaySubtitle(reviewsCount, mode);
  const automateHref = `/automations/new?play=${encodeURIComponent(id)}`;
  const canDispatch =
    ctaLayout.showRunNow && !!pattern && typeof onSubmit === "function";

  return (
    <div
      className={
        "rounded-lg border bg-white/[0.02] p-3 transition " +
        (expanded
          ? "border-aqua/40 bg-aqua/[0.04]"
          : "border-white/10 hover:border-white/25")
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">{title}</p>
          <p className="mt-0.5 text-[11px] text-white/55">{subtitle}</p>
          {description ? (
            <p className="mt-1.5 line-clamp-2 text-[11px] text-white/45">
              {description}
            </p>
          ) : null}
          {tags && tags.length > 0 ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-1">
              {tags.map((t) => (
                <Badge key={t} tone="neutral">
                  {t}
                </Badge>
              ))}
              <span className="font-mono text-[10px] text-white/40">{id}</span>
            </div>
          ) : (
            <span className="mt-1.5 inline-block font-mono text-[10px] text-white/40">
              {id}
            </span>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center justify-end gap-2">
        {canDispatch ? (
          <button
            type="button"
            onClick={onToggle}
            className={
              "shrink-0 rounded-full border px-3.5 py-1 text-[11px] font-bold transition " +
              (expanded
                ? "border-white/25 bg-white/[0.06] text-white/75 hover:bg-white/[0.10]"
                : "border-aqua/60 bg-aqua/80 text-ink hover:bg-aqua")
            }
          >
            {expanded ? "Cancel" : "Run now"}
          </button>
        ) : null}
        {ctaLayout.showAutomate ? (
          <Link
            href={automateHref}
            title="Schedule this play to run on a cadence."
            className={
              "shrink-0 rounded-full border px-3.5 py-1 text-[11px] font-bold transition " +
              (canDispatch
                ? "border-white/20 bg-white/[0.04] text-white/75 hover:border-white/35 hover:text-white"
                : "border-aqua/60 bg-aqua/80 text-ink hover:bg-aqua")
            }
          >
            Automate
          </Link>
        ) : null}
      </div>

      {expanded && pattern && onSubmit ? (
        <PlayCardForm pattern={pattern} state={state} onSubmit={onSubmit} />
      ) : null}
    </div>
  );
}

/**
 * Inputs form for a one-shot dispatch — moved verbatim from the old
 * ``requests-catalog.tsx :: PatternForm``. Keeps the same submit
 * payload shape so the existing ``POST /api/requests`` wiring keeps
 * working for both ``/plays`` and ``/requests``.
 */
function PlayCardForm({
  pattern,
  state,
  onSubmit,
}: {
  pattern: ApiCatalogPattern;
  state: CardState;
  onSubmit: (inputs: Record<string, string>) => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => {
    const seeded: Record<string, string> = {};
    for (const input of pattern.inputs) {
      if (typeof input.default === "string") {
        seeded[input.name] = input.default;
      }
    }
    return seeded;
  });

  const missing = pattern.inputs
    .filter((i) => i.required && !(values[i.name] ?? "").trim())
    .map((i) => i.name);

  const canSubmit = state.mode !== "saving" && missing.length === 0;

  return (
    <form
      className="mt-4 space-y-3 border-t border-white/10 pt-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        const cleaned: Record<string, string> = {};
        for (const [k, v] of Object.entries(values)) {
          const trimmed = v.trim();
          if (trimmed) cleaned[k] = trimmed;
        }
        onSubmit(cleaned);
      }}
    >
      {pattern.inputs.length === 0 ? (
        <p className="text-[11px] text-white/55">
          This play doesn&rsquo;t take any inputs — hit Dispatch to run
          it against the selected repo.
        </p>
      ) : (
        pattern.inputs.map((input) => (
          <InputField
            key={input.name}
            input={input}
            value={values[input.name] ?? ""}
            onChange={(next) =>
              setValues((prev) => ({ ...prev, [input.name]: next }))
            }
          />
        ))
      )}

      {state.mode === "error" ? (
        <div className="rounded-md border border-coral/40 bg-coral/10 px-3 py-2 text-xs text-coral">
          {state.message}
        </div>
      ) : null}

      <div className="flex items-center justify-end gap-2">
        <button
          type="submit"
          disabled={!canSubmit}
          className={
            "rounded-md border px-4 py-1.5 text-xs font-semibold transition " +
            (canSubmit
              ? "border-aqua/50 bg-aqua/15 text-aqua hover:bg-aqua/25"
              : "cursor-not-allowed border-white/15 bg-white/[0.04] text-white/45")
          }
        >
          {state.mode === "saving" ? "Dispatching…" : "Dispatch"}
        </button>
      </div>
    </form>
  );
}

function InputField({
  input,
  value,
  onChange,
}: {
  input: ApiPatternInput;
  value: string;
  onChange: (next: string) => void;
}) {
  const kind = (input.type ?? "text").toLowerCase();
  const label = input.name + (input.required ? " *" : "");

  if (kind === "enum" && Array.isArray(input.values) && input.values.length > 0) {
    return (
      <Field label={label} hint={input.hint}>
        <select
          value={value || input.default || ""}
          onChange={(e) => onChange(e.target.value)}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 text-sm text-white focus:border-aqua focus:outline-none"
        >
          {!input.required ? <option value="">(unset)</option> : null}
          {input.values.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
      </Field>
    );
  }

  if (kind === "multiline") {
    return (
      <Field label={label} hint={input.hint}>
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={4}
          className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-2 font-mono text-[13px] leading-relaxed text-white focus:border-aqua focus:outline-none"
        />
      </Field>
    );
  }

  return (
    <Field label={label} hint={input.hint}>
      <input
        type={kind === "url" ? "url" : "text"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={input.default ?? ""}
        className="w-full rounded-md border border-white/15 bg-black/30 px-3 py-1.5 font-mono text-sm text-white focus:border-aqua focus:outline-none"
      />
    </Field>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <label className="block text-[10px] font-semibold uppercase tracking-widest text-white/55">
        {label}
      </label>
      <div className="mt-1">{children}</div>
      {hint ? <p className="mt-1 text-[11px] text-white/45">{hint}</p> : null}
    </div>
  );
}
