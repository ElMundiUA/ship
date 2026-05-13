import { cn } from "@/lib/cn";

/**
 * CSS-only horizontal progress bar for the Coverage list.
 *
 * No client JS, no chart library — just a fixed-height track with a
 * coloured fill whose width is bound to ``pct`` (0.0–1.0). Colour is
 * picked from the (critical, pct) pair so the eye can scan a long
 * list and immediately spot red-zero rows:
 *
 * - 0%               → coral fill (with a 1px floor stripe so the user
 *                      can still see "this play has zero coverage";
 *                      a fully empty bar is indistinguishable from a
 *                      missing one)
 * - critical && <100% → coral fill
 * - 0 < pct < 100%   → sun (amber) fill
 * - pct === 100%     → emerald fill
 *
 * The bar carries an inline overlay with the
 * ``X/Y repos (Z%)`` label so the row stays scannable at a glance.
 */

export function CoverageProgressBar({
  pct,
  critical,
  covered,
  total,
}: {
  /** 0.0–1.0; backend already clamps. */
  pct: number;
  /** Render coral (red) fill when ``pct < 1.0``. */
  critical: boolean;
  /** Numerator for the overlay ("X / Y repos"). */
  covered: number;
  /** Denominator for the overlay. ``0`` keeps the bar visible but renders "—". */
  total: number;
}) {
  const clamped = Math.min(1, Math.max(0, pct));
  const percentLabel = Math.round(clamped * 100);
  // Floor the visible width at 2% so 0% rows still show the coral
  // sliver — see component comment for the why.
  const widthPct = clamped === 0 ? 2 : Math.max(2, clamped * 100);

  let fillCls: string;
  if (clamped >= 1) {
    fillCls = "bg-emerald-400/80";
  } else if (critical || clamped === 0) {
    fillCls = "bg-coral/80";
  } else {
    fillCls = "bg-sun/80";
  }

  return (
    <div
      className="relative h-6 w-full overflow-hidden rounded-md border border-white/10 bg-white/[0.04]"
      role="progressbar"
      aria-valuenow={percentLabel}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-label={`${covered} of ${total} repos covered`}
    >
      <div
        className={cn("absolute inset-y-0 left-0 transition-all", fillCls)}
        style={{ width: `${widthPct}%` }}
      />
      <div className="relative z-10 flex h-full items-center justify-between px-2 text-[11px] font-semibold text-white/85 mix-blend-luminosity">
        <span className="truncate">
          {total === 0
            ? "No activated repos"
            : `${covered}/${total} repos`}
        </span>
        <span className="tabular-nums">{percentLabel}%</span>
      </div>
    </div>
  );
}
