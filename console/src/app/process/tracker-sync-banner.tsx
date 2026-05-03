"use client";

/**
 * Tracker projection sync — operator-facing entry point.
 *
 * Replaces the manual ``TrackerProjectionsTable`` (deleted in commit
 * 8193772) with a single-button "align with my tracker" flow. Submits
 * to ``/api/process/tracker-sync`` (Next.js proxy), which calls the
 * backend's ``POST .../tracker-sync`` route. The backend probes the
 * bound tracker, runs the deterministic + LLM resolver with
 * validation+retry, opens a PR rewriting ``process.tracker_mapping``
 * in ``.ship/config.yml``, and 303-redirects the operator to the PR.
 *
 * Hidden when:
 * - no repo is selected (sync needs a concrete ``.ship/config.yml``
 *   to rewrite),
 * - no tracker is bound to the workspace,
 * - or the tracker isn't yet wired on the backend (currently
 *   Linear-only).
 *
 * Visible-but-disabled isn't useful here — operators don't need an
 * "explanation banner" when they have nothing to act on, the absence
 * of the button is the signal.
 */
export function TrackerSyncBanner({
  workspaceId,
  processId,
  repoId,
  trackerKind,
}: {
  workspaceId: string;
  processId: string;
  repoId?: string;
  trackerKind?: string;
}) {
  if (!repoId) return null;
  if (trackerKind !== "linear") return null;

  return (
    <form
      action="/api/process/tracker-sync"
      method="post"
      className="flex items-center justify-between gap-3 rounded-lg border border-aqua/15 bg-aqua/[0.04] px-3 py-2 text-xs"
    >
      <input type="hidden" name="workspaceId" value={workspaceId} />
      <input type="hidden" name="processId" value={processId} />
      <input type="hidden" name="repoId" value={repoId} />
      <div className="min-w-0 flex-1">
        <p className="font-bold text-white/85">
          Align tracker columns with your workflow
        </p>
        <p className="mt-0.5 text-[11px] text-white/55">
          Probes Linear for the team&apos;s actual workflow states, lets
          the model fill any gaps the canonical defaults don&apos;t
          cover, then opens a PR against{" "}
          <code className="font-mono">.ship/config.yml</code> so you
          review and merge — never edit the mapping by hand.
        </p>
      </div>
      <button
        type="submit"
        className="shrink-0 rounded-full border border-aqua/35 bg-aqua/15 px-3 py-1 text-[11px] font-bold text-aqua transition hover:bg-aqua/20"
      >
        Sync with Linear →
      </button>
    </form>
  );
}
