"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import { ButtonGhost, ButtonPrimary, Card } from "./ui";

/**
 * Standard "the backend is unreachable" surface.
 *
 * Used wherever the console used to silently fall back to mock data when
 * the live ``/v1`` API was unavailable. Showing real failure beats showing
 * a polished lie — production users must never confuse mock data for live
 * state.
 *
 * - ``scope`` — short label naming what failed (``workspace``, ``inbox``,
 *   ``knowledge``…). Surfaces in the headline and in error logs.
 * - ``retryHref`` — optional URL the retry button navigates to. Defaults to
 *   ``window.location.reload()``.
 * - ``details`` — technical detail (typically the error ``message`` from
 *   :class:`ApiHttpError` / :class:`ApiUnavailableError`). Hidden behind a
 *   "What this means" toggle so users see calm copy by default.
 */
export function ApiUnavailable({
  scope,
  retryHref,
  details,
  className,
}: {
  scope: string;
  retryHref?: string;
  details?: string;
  className?: string;
}) {
  const [showDetails, setShowDetails] = useState(false);
  const handleRetry = () => {
    if (retryHref) {
      window.location.href = retryHref;
    } else {
      window.location.reload();
    }
  };
  return (
    <Card className={cn("grid place-items-center py-12 text-center", className)}>
      <div className="max-w-md">
        <div className="inline-flex items-center gap-2 rounded-full border border-coral/30 bg-coral/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-widest text-coral">
          <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-coral" />
          {scope} unavailable
        </div>
        <h4 className="mt-4 font-display text-lg font-bold text-white">
          Ship can&rsquo;t reach this surface right now
        </h4>
        <p className="mt-2 text-sm text-white/65">
          The backend did not respond. This is usually transient — retry in a
          few seconds. If it persists, status updates land at{" "}
          <a
            href="https://status.ship.elmundi.com"
            target="_blank"
            rel="noopener noreferrer"
            className="text-aqua underline-offset-2 hover:underline"
          >
            status.ship.elmundi.com
          </a>
          .
        </p>
        <div className="mt-5 flex items-center justify-center gap-2">
          <ButtonPrimary onClick={handleRetry}>Retry</ButtonPrimary>
          <ButtonGhost onClick={() => setShowDetails((prev) => !prev)}>
            {showDetails ? "Hide details" : "What this means"}
          </ButtonGhost>
        </div>
        {showDetails && (
          <div className="mt-4 rounded-xl border border-white/10 bg-black/30 px-4 py-3 text-left">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-white/45">
              Technical detail
            </p>
            <code className="mt-1 block break-words font-mono text-xs text-white/75">
              scope: {scope}
              {details ? ` · ${details}` : ""}
            </code>
            <p className="mt-3 text-xs leading-relaxed text-white/55">
              The console reached the server but did not get a usable response.
              The data shown above this card may be stale or empty until the
              connection recovers.
            </p>
          </div>
        )}
      </div>
    </Card>
  );
}
