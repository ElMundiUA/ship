"use client";

/**
 * Pure presentational "what just happened" card for one repo.
 *
 * Renders the wizard_seed result: PR link, branch, bundle version,
 * tracker kind, file count. Nothing fetches here — the parent
 * (:component:`DoneResult`) hands in everything we need so the card
 * is trivial to unit-test and reuse in fallback paths.
 *
 * The PR-state badge ("opened" vs "merged") is the one piece of
 * state that *can* change between the wizard finishing and this
 * card rendering: the configure step's "Activate Ship now" modal
 * may have merged the PR via the App's installation token. The
 * activation handler stashes the resulting ``{merged, sha}`` under
 * ``ship.wizard_seed_activated.{repo_id}``; we read it once on
 * mount. No GitHub round-trip — the source of truth is the
 * activation API's response, captured at the moment the merge
 * happened. The badge stays at "opened" when the operator picked
 * "I'll merge it myself" (no activation key written) or when
 * sessionStorage is unavailable (private browsing).
 *
 * Pre-cleanup this card also surfaced CODEOWNERS-derived routing
 * rules and a repo-intel poll badge. Both fields became dead
 * post-P5-06: the wizard_seed route stopped dispatching either
 * (post-merge bootstrap/config sync owns all repo-analysis side
 * effects now), so ``codeowners`` and ``intel`` arrive ``null`` /
 * never. The dead UX showed operators "harvest never dispatched —
 * Retry harvest →" buttons that wired to nothing. Cleanup keeps the
 * card to load-bearing facts only.
 */

import { useEffect, useState } from "react";

import type {
  ApiActivatedRepo,
  ApiWizardSeedOut,
} from "@/lib/api/client";

export function RepoResultCard({
  repo,
  result,
  workspaceId,
}: {
  /** Best-effort repo metadata. ``null`` when only the wizard payload is around. */
  repo: ApiActivatedRepo | null;
  result: ApiWizardSeedOut;
  workspaceId: string | null;
}) {
  const fullName = repo?.full_name ?? null;
  const defaultBranch = repo?.default_branch ?? "main";
  const bundleVersion = repo?.installed_bundle_version ?? null;
  const fileCount = result.files.length;

  return (
    <article
      data-testid="onboarding-done-repo-card"
      data-repo-id={repo?.id ?? ""}
      data-repo-full-name={fullName ?? ""}
      className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card"
    >
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-display text-lg font-bold text-white">
            {fullName ?? "(repo)"}
          </h3>
          <p className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-white/55">
            <span>
              Default branch:{" "}
              <code className="rounded bg-white/5 px-1 text-aqua">
                {defaultBranch}
              </code>
            </span>
            {bundleVersion && (
              <span className="rounded bg-aqua/15 px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-widest text-aqua">
                bundle v{bundleVersion}
              </span>
            )}
          </p>
        </div>
        {result.tracker_kind && (
          <span className="rounded-full border border-white/10 bg-white/[0.04] px-2 py-0.5 text-[10px] uppercase tracking-widest text-white/65">
            tracker · {result.tracker_kind}
          </span>
        )}
      </header>

      <div className="mt-4 space-y-3">
        <PullRequestRow
          result={result}
          repoId={repo?.id ?? null}
          workspaceId={workspaceId}
        />
        <FileCountRow result={result} fileCount={fileCount} />
      </div>
    </article>
  );
}

function Section({
  label,
  children,
  testId,
}: {
  label: string;
  children: React.ReactNode;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className="rounded-xl border border-white/5 bg-white/[0.025] p-3"
    >
      <div className="text-[10px] font-bold uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="mt-1.5 text-xs text-white/80">{children}</div>
    </div>
  );
}

function PullRequestRow({
  result,
  repoId,
  workspaceId,
}: {
  result: ApiWizardSeedOut;
  /** ``null`` when the parent only had the wizard payload, no repo row. */
  repoId: string | null;
  workspaceId: string | null;
}) {
  // Seed the merged signal from the live payload too — covers the
  // "open Done from fresh tab after the PR was already merged" case
  // immediately, no polling round-trip needed.
  const polled = useActivationMerged(workspaceId, repoId);
  const merged = polled || result.merged === true;
  return (
    <Section label="Pull request">
      <div className="flex flex-wrap items-center gap-2">
        <a
          href={result.pr_url}
          target="_blank"
          rel="noreferrer"
          data-testid="onboarding-done-pr-link"
          className="inline-flex items-center gap-1.5 rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1 text-[11px] font-bold text-aqua hover:bg-aqua/[0.16]"
        >
          → #{result.pr_number} Ship: bootstrap (open in GitHub)
        </a>
        {merged ? (
          <span
            data-testid="onboarding-done-pr-state"
            className="rounded-full border border-violet-400/30 bg-violet-500/[0.10] px-2 py-0.5 text-[10px] uppercase tracking-widest text-violet-300"
          >
            merged
          </span>
        ) : (
          <span
            data-testid="onboarding-done-pr-state"
            className="rounded-full border border-emerald-400/30 bg-emerald-500/[0.08] px-2 py-0.5 text-[10px] uppercase tracking-widest text-emerald-300"
          >
            opened
          </span>
        )}
      </div>
      <div className="mt-2 text-[11px] text-white/55">
        Branch:{" "}
        <code className="rounded bg-white/5 px-1 text-white/80">
          {result.branch}
        </code>
      </div>
    </Section>
  );
}


/**
 * ELS-182 (W6) — derive the "merged" badge from two signals:
 *
 *   1. sessionStorage flag from the activation modal (fast path —
 *      operator just merged via the App's installation token; no
 *      polling round-trip needed).
 *   2. Backend ``GET .../wizard_seed/latest`` poll. Catches the
 *      "opened a fresh tab" + "merged on github.com manually" cases
 *      that sessionStorage doesn't cover. Polls every 10s for up to
 *      2 min after mount; gives up after the timeout to avoid
 *      lingering work on a dead tab.
 */
function useActivationMerged(
  workspaceId: string | null,
  repoId: string | null,
): boolean {
  const [merged, setMerged] = useState(false);

  // Fast path: sessionStorage.
  useEffect(() => {
    if (!repoId) return;
    if (typeof window === "undefined") return;
    let raw: string | null = null;
    try {
      raw = window.sessionStorage.getItem(
        `ship.wizard_seed_activated.${repoId}`,
      );
    } catch {
      return;
    }
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === "object" && parsed.merged === true) {
        setMerged(true);
      }
    } catch {
      // Tampered cache — ignore; badge stays OPENED.
    }
  }, [repoId]);

  // Slow path: backend poll. Stops as soon as we flip merged=true
  // (either source) or after 2 minutes.
  useEffect(() => {
    if (!workspaceId || !repoId) return;
    if (merged) return;
    let cancelled = false;
    const started = Date.now();
    const POLL_INTERVAL_MS = 10_000;
    const POLL_TIMEOUT_MS = 120_000;

    async function check() {
      if (cancelled) return;
      try {
        const { getLatestWizardSeed } = await import("@/lib/api/client");
        const latest = await getLatestWizardSeed(workspaceId!, repoId!);
        if (!cancelled && latest?.merged === true) {
          setMerged(true);
          return;
        }
      } catch {
        // 404 (no seed) / 5xx / network — silently retry until timeout.
      }
      if (cancelled) return;
      if (Date.now() - started >= POLL_TIMEOUT_MS) return;
      window.setTimeout(check, POLL_INTERVAL_MS);
    }

    // First check fires immediately (so a fresh tab with an already-
    // merged PR doesn't wait 10s for the right badge).
    void check();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, repoId, merged]);

  return merged;
}

function FileCountRow({
  result,
  fileCount,
}: {
  result: ApiWizardSeedOut;
  fileCount: number;
}) {
  // ``result.files`` is the canonical list — show it inline so the
  // operator sees what actually committed, not just a count. Most
  // bootstraps land 4 files (workflow + config + state + FSM) so the
  // list is short enough to render inline.
  return (
    <Section label={`${fileCount} file${fileCount === 1 ? "" : "s"} committed`}>
      <ul className="space-y-1 text-[11px] text-white/70">
        {result.files.map((path) => (
          <li key={path}>
            <code className="rounded bg-white/5 px-1.5 py-0.5 text-white/80">
              {path}
            </code>
          </li>
        ))}
      </ul>
    </Section>
  );
}
