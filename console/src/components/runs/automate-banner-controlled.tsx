"use client";

/**
 * Client wrapper for :func:`AutomateBanner` (RFC-0010 P4-04).
 *
 * Owns the per-run dismiss state. We picked **Option B** from the
 * ticket spec — pure client-side dismissal backed by
 * ``localStorage`` — over Option A's server roundtrip:
 *
 *   - the dismissal carries no audit value (it's a UI-state nudge,
 *     not a workflow decision),
 *   - it stays operator-local (different members may not have
 *     dismissed the same banner), which matches the storage shape
 *     we'd want anyway, and
 *   - it avoids a backend endpoint that would otherwise need to
 *     own a new ``dismissed_run_banners`` table just for one CTA.
 *
 * If we ever want cross-device dismissal we can revisit; until
 * then, ``localStorage`` is the right ergonomics.
 */

import { useEffect, useState } from "react";

import {
  AutomateBanner,
  type AutomateBannerData,
} from "./automate-banner";

const STORAGE_PREFIX = "dismissed_automate_run_";

export function AutomateBannerControlled({
  runId,
  data,
}: {
  runId: string;
  /**
   * Resolved banner shape (or ``null`` when the banner shouldn't
   * render at all). The page short-circuits and skips rendering
   * this component when the resolver returns ``null``; we still
   * accept ``null`` here so callers can render unconditionally
   * without an outer ternary.
   */
  data: AutomateBannerData | null;
}) {
  const storageKey = `${STORAGE_PREFIX}${runId}`;
  // Hydration: assume "not dismissed" on the server, then read
  // localStorage on mount. This avoids an SSR mismatch (the server
  // can't read localStorage) and is fine UX-wise — the banner
  // briefly mounts then unmounts on dismissed visits, which reads
  // as "the page just settled" rather than "the banner flashed".
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    try {
      setDismissed(window.localStorage.getItem(storageKey) === "1");
    } catch {
      // localStorage can throw in private mode / quota exhaustion;
      // safest fallback is to leave the banner visible.
    }
  }, [storageKey]);

  if (!data) return null;
  if (dismissed) return null;

  // The "automated" variant is informational and doesn't carry a
  // dismiss button per the ticket UI ("[View automation →]" only),
  // so we don't pass an ``onDismiss`` handler in that branch.
  if (data.variant === "automated") {
    return <AutomateBanner data={data} />;
  }

  return (
    <AutomateBanner
      data={data}
      onDismiss={() => {
        try {
          window.localStorage.setItem(storageKey, "1");
        } catch {
          // Same fallback as above; the in-memory dismissal still
          // wins for this page render so the operator gets
          // immediate feedback even if persistence fails.
        }
        setDismissed(true);
      }}
    />
  );
}
