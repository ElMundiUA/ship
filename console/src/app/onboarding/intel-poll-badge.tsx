"use client";

/**
 * Polling badge for the post-onboarding "What just happened" page (P5-09).
 *
 * Renders one of four states depending on the wizard intel handle and
 * any successfully-polled :type:`ApiRepoIntel` row:
 *
 *  - ``"harvesting"`` (aqua, in-progress) — handle was ``enqueued`` and
 *    we have no row yet. Polls :func:`getCurrentRepoIntel` every 5s
 *    for up to 3 minutes.
 *  - ``"timeout"`` (amber) — 3 minutes elapsed without a row. Surfaces
 *    a "check back later" hint.
 *  - ``"done"`` (green) — we have a row. Renders a small preview:
 *    top-3 languages + the primary framework + structure summary.
 *  - ``"failed"`` (coral) — :attr:`ApiRepoIntel.harvest_error` is
 *    non-null on the latest row OR (if no row) the harvest never
 *    enqueued. Offers a "Retry" button POSTing to
 *    :func:`triggerRepoIntelHarvest`.
 *
 * Polling cadence chosen to balance UX (operators want to see the
 * preview pop while they're still on the page) against backend load
 * (one repo = 36 reads worst case, well under the per-token rate limit
 * for this surface). All polling stops the moment the component
 * unmounts (cleared interval, ``AbortController`` for in-flight
 * requests) so navigating away doesn't leak a request.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type {
  ApiRepoIntel,
  ApiRepoIntelHarvestHandle,
  ApiWizardSeedIntelHandle,
} from "@/lib/api/client";

/** Poll interval. Conservative — repo-intel writes are infrequent. */
const POLL_INTERVAL_MS = 5_000;
/** Max wait before flipping to ``timeout``. 3 minutes covers a slow harvest. */
const POLL_TIMEOUT_MS = 3 * 60_000;

type BadgeState =
  | { kind: "harvesting" }
  | { kind: "done"; intel: ApiRepoIntel }
  | { kind: "failed"; error: string }
  | { kind: "timeout" };

export function IntelPollBadge({
  workspaceId,
  repoId,
  handle,
}: {
  workspaceId: string | null;
  repoId: string;
  handle: ApiWizardSeedIntelHandle | null;
}) {
  // Initial state derived from the handle. ``intel_id`` populated
  // means the wizard already wrote the row inline (dev path) — we
  // skip the harvesting phase entirely.
  const initial: BadgeState = (() => {
    if (handle == null) {
      return { kind: "failed", error: "harvest never dispatched" };
    }
    if (handle.intel_id != null) {
      return { kind: "harvesting" }; // we still need the row, fetch once
    }
    if (!handle.enqueued && handle.intel_id == null) {
      return {
        kind: "failed",
        error: "harvest dispatch failed; retry to try again",
      };
    }
    return { kind: "harvesting" };
  })();

  const [state, setState] = useState<BadgeState>(initial);
  const [retrying, setRetrying] = useState(false);
  const startedAt = useRef<number>(Date.now());

  /** Re-trigger the harvest. Resets the poll deadline + state. */
  const retry = useCallback(async () => {
    if (workspaceId == null || retrying) return;
    setRetrying(true);
    try {
      const res = await fetch("/api/onboard/intel-harvest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          workspace_id: workspaceId,
          repo_id: repoId,
        }),
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(detail || `HTTP ${res.status}`);
      }
      // We don't need the response body — :type:`ApiRepoIntelHarvestHandle`
      // is the same shape the wizard sent us originally and we're
      // about to start polling either way.
      void (res.json() as Promise<{ handle?: ApiRepoIntelHarvestHandle }>);
      startedAt.current = Date.now();
      setState({ kind: "harvesting" });
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "couldn't dispatch harvest";
      setState({ kind: "failed", error: msg });
    } finally {
      setRetrying(false);
    }
  }, [workspaceId, repoId, retrying]);

  useEffect(() => {
    if (state.kind !== "harvesting") return;
    if (workspaceId == null) return;

    const controller = new AbortController();
    let cancelled = false;

    const poll = async () => {
      try {
        const qs = new URLSearchParams({
          workspace_id: workspaceId,
          repo_id: repoId,
        });
        const res = await fetch(
          `/api/onboard/intel-current?${qs.toString()}`,
          { credentials: "include", signal: controller.signal },
        );
        if (cancelled) return;
        // 404 = "not yet"; everything else gets the same treatment
        // (transient poll failures fall through and the timeout arm
        // below eventually flips the badge to "timeout" if nothing
        // ever lands).
        if (res.status === 404) {
          // expected during the harvesting phase — fall through.
        } else if (res.ok) {
          const body = (await res.json()) as { intel?: ApiRepoIntel };
          if (cancelled) return;
          const intel = body.intel;
          if (intel) {
            if (intel.harvest_error) {
              setState({ kind: "failed", error: intel.harvest_error });
              return;
            }
            setState({ kind: "done", intel });
            return;
          }
        }
      } catch {
        // Aborted / network blip / backend hiccup. Keep polling
        // until the timeout arm fires.
        if (cancelled) return;
      }
      if (cancelled) return;
      if (Date.now() - startedAt.current > POLL_TIMEOUT_MS) {
        setState({ kind: "timeout" });
      }
    };

    // Fire one immediate read so the inline-harvest case (dev) lights
    // up the preview without waiting a full 5s for the first tick.
    void poll();
    const id = setInterval(poll, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(id);
    };
  }, [state.kind, workspaceId, repoId]);

  if (state.kind === "harvesting") {
    return (
      <div className="rounded-xl border border-aqua/30 bg-aqua/[0.06] p-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2 w-2 animate-pulse rounded-full bg-aqua" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-aqua">
            Harvesting…
          </span>
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-white/60">
          We&apos;re scanning the repo to write{" "}
          <code className="rounded bg-white/5 px-1 text-aqua">
            .ship/knowledge/repo-intel.md
          </code>
          . Once it lands, agents stop re-scanning the repo on every
          run and read the snapshot instead. Usually under 2 minutes.
        </p>
      </div>
    );
  }

  if (state.kind === "timeout") {
    return (
      <div className="rounded-xl border border-sun/40 bg-sun/[0.08] p-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2 w-2 rounded-full bg-sun" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-sun">
            Taking longer than expected
          </span>
        </div>
        <p className="mt-1.5 text-[11px] leading-relaxed text-white/65">
          Harvest hasn&apos;t completed after 3 minutes. Check back later
          from the repo&apos;s settings page, or retry below.
        </p>
        <button
          type="button"
          onClick={retry}
          disabled={retrying}
          className="mt-2 inline-flex rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-semibold text-aqua hover:bg-aqua/[0.16] disabled:opacity-50"
        >
          {retrying ? "Retrying…" : "Retry harvest →"}
        </button>
      </div>
    );
  }

  if (state.kind === "failed") {
    return (
      <div className="rounded-xl border border-coral/40 bg-coral/10 p-3">
        <div className="flex items-center gap-2">
          <span className="inline-flex h-2 w-2 rounded-full bg-coral" />
          <span className="text-[11px] font-bold uppercase tracking-widest text-coral">
            Harvest failed
          </span>
        </div>
        <p className="mt-1.5 break-words text-[11px] leading-relaxed text-white/70">
          {state.error}
        </p>
        <button
          type="button"
          onClick={retry}
          disabled={retrying}
          className="mt-2 inline-flex rounded-full border border-coral/40 bg-coral/10 px-3 py-1 text-[11px] font-semibold text-coral hover:bg-coral/20 disabled:opacity-50"
        >
          {retrying ? "Retrying…" : "Retry harvest →"}
        </button>
      </div>
    );
  }

  // state.kind === "done"
  return <IntelDonePreview intel={state.intel} />;
}

function IntelDonePreview({ intel }: { intel: ApiRepoIntel }) {
  const topLanguages = Object.entries(intel.languages || {})
    .sort((a, b) => (Number(b[1]) || 0) - (Number(a[1]) || 0))
    .slice(0, 3);
  const primaryFramework = intel.frameworks?.[0] ?? null;
  const structure = intel.structure as
    | { file_count?: number; depth_p50?: number; top_level_dirs?: unknown[] }
    | undefined;
  const fileCount =
    typeof structure?.file_count === "number" ? structure.file_count : null;

  return (
    <div className="rounded-xl border border-emerald-400/30 bg-emerald-500/[0.06] p-3">
      <div className="flex items-center gap-2">
        <span className="inline-flex h-2 w-2 rounded-full bg-emerald-300" />
        <span className="text-[11px] font-bold uppercase tracking-widest text-emerald-300">
          Repo intel ready
        </span>
      </div>
      <div className="mt-2 grid grid-cols-1 gap-1.5 text-[11px] text-white/75 sm:grid-cols-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest text-white/45">
            Top languages
          </div>
          <div className="mt-0.5">
            {topLanguages.length > 0
              ? topLanguages
                  .map(
                    ([name, share]) =>
                      `${name} ${Math.round((Number(share) || 0) * 100)}%`,
                  )
                  .join(" · ")
              : "—"}
          </div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-widest text-white/45">
            Primary framework
          </div>
          <div className="mt-0.5">{primaryFramework ?? "—"}</div>
        </div>
        <div>
          <div className="text-[10px] uppercase tracking-widest text-white/45">
            Files scanned
          </div>
          <div className="mt-0.5">
            {fileCount != null ? fileCount.toLocaleString() : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
