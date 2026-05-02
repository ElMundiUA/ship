"use client";

/**
 * Client wrapper for one repo on the post-onboarding done page (P5-09).
 *
 * Resolution order:
 *
 *   1. ``sessionStorage["ship.wizard_seed_result.<repo_id>"]`` — set
 *      by the configure step (sibling P5-08) the moment the wizard
 *      seed succeeds. This is the happy path — the user just clicked
 *      "I'm done" and we have the full :type:`ApiWizardSeedOut` cached
 *      client-side.
 *   2. ``GET /v1/workspaces/{ws}/repos/{repo}/wizard_seed/latest`` —
 *      durable fallback when sessionStorage is empty (tab reload,
 *      opened on another device, copied URL). Returns the same shape
 *      from the most recent ``repo.wizard_seed`` audit row.
 *   3. ``404`` from the fallback → render an "empty" CTA pointing the
 *      operator back to the configure step. We don't auto-trigger a
 *      seed because the wizard's tracker / secrets gates exist for a
 *      reason; a "click to retry" feels safer than a silent re-seed.
 *
 * Invariant: never mutates ``sessionStorage``. The configure step
 * owns writes; we own reads + the API fallback. Cleaning up the
 * cached entry would race with multi-tab flows.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import type {
  ApiActivatedRepo,
  ApiWizardSeedOut,
} from "@/lib/api/client";

import { RepoResultCard } from "./repo-result-card";

const STORAGE_KEY_PREFIX = "ship.wizard_seed_result.";

/**
 * sessionStorage key shared with sibling P5-08 (the configure step).
 * Mirrors :func:`wizardSeedResultStorageKey` in ``repo-card.tsx``;
 * duplicated rather than imported because that module is
 * ``"use client"`` and pulling it transitively into this file would
 * drag the whole RepoCard tree into the done page bundle.
 */
function storageKeyFor(repoId: string): string {
  return `${STORAGE_KEY_PREFIX}${repoId}`;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ready"; result: ApiWizardSeedOut }
  | { kind: "missing" }
  | { kind: "error"; message: string };

export function DoneResult({
  workspaceId,
  repo,
}: {
  workspaceId: string | null;
  repo: ApiActivatedRepo;
}) {
  const [state, setState] = useState<LoadState>({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;

    const cached = readFromSessionStorage(repo.id);
    if (cached) {
      setState({ kind: "ready", result: cached });
      return;
    }

    if (workspaceId == null) {
      setState({ kind: "missing" });
      return;
    }

    void (async () => {
      try {
        const qs = new URLSearchParams({
          workspace_id: workspaceId,
          repo_id: repo.id,
        });
        const res = await fetch(
          `/api/onboard/wizard-seed-latest?${qs.toString()}`,
          { credentials: "include" },
        );
        if (cancelled) return;
        if (res.status === 404) {
          setState({ kind: "missing" });
          return;
        }
        if (!res.ok) {
          const detail = await res.text().catch(() => "");
          setState({
            kind: "error",
            message: detail || `HTTP ${res.status}`,
          });
          return;
        }
        const body = (await res.json()) as { result?: ApiWizardSeedOut };
        if (cancelled) return;
        if (!body.result) {
          setState({ kind: "missing" });
          return;
        }
        setState({ kind: "ready", result: body.result });
      } catch (err) {
        if (cancelled) return;
        const msg =
          err instanceof Error
            ? err.message
            : "Couldn't load the wizard result.";
        setState({ kind: "error", message: msg });
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [workspaceId, repo.id]);

  if (state.kind === "loading") {
    return <DoneSkeleton repoFullName={repo.full_name} />;
  }

  if (state.kind === "missing") {
    return <NoBootstrapYet workspaceId={workspaceId} repo={repo} />;
  }

  if (state.kind === "error") {
    return (
      <div className="rounded-2xl border border-coral/40 bg-coral/10 p-4 text-xs text-coral">
        Couldn&apos;t load the bootstrap result for{" "}
        <strong>{repo.full_name}</strong>: {state.message}
      </div>
    );
  }

  return <RepoResultCard repo={repo} result={state.result} />;
}

function DoneSkeleton({ repoFullName }: { repoFullName: string }) {
  return (
    <div
      role="status"
      aria-busy="true"
      className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card"
    >
      <div className="flex items-center justify-between">
        <h3 className="font-display text-lg font-bold text-white/80">
          {repoFullName}
        </h3>
        <span className="text-[11px] text-white/45">loading…</span>
      </div>
      <div className="mt-3 space-y-2">
        <div className="h-12 w-full animate-pulse rounded-lg bg-white/[0.03]" />
        <div className="h-12 w-full animate-pulse rounded-lg bg-white/[0.03]" />
        <div className="h-12 w-full animate-pulse rounded-lg bg-white/[0.03]" />
      </div>
    </div>
  );
}

function NoBootstrapYet({
  workspaceId,
  repo,
}: {
  workspaceId: string | null;
  repo: ApiActivatedRepo;
}) {
  // Link to the current ``confirm`` step rather than the deprecated
  // ``configure`` legacy id (the page redirects ?step=configure →
  // ?step=confirm anyway, but pointing at the live name keeps the URL
  // bar honest).
  const confirmHref = workspaceId
    ? `/onboarding?step=confirm&ws=${encodeURIComponent(workspaceId)}`
    : "/onboarding";
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-5 backdrop-blur-xl shadow-card">
      <h3 className="font-display text-lg font-bold text-white">
        {repo.full_name}
      </h3>
      <p className="mt-1 text-xs text-white/65">
        No bootstrap yet for this repo. Head back to Confirm and open
        the seed PR — once it lands, this page will show the result.
      </p>
      <Link
        href={confirmHref}
        className="mt-3 inline-flex rounded-full border border-aqua/40 bg-aqua/[0.08] px-3 py-1.5 text-[11px] font-bold text-aqua hover:bg-aqua/[0.16]"
      >
        → Back to Confirm
      </Link>
    </div>
  );
}

function readFromSessionStorage(repoId: string): ApiWizardSeedOut | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.sessionStorage.getItem(storageKeyFor(repoId));
  } catch {
    // Private browsing / disabled storage. Fall back to the API path.
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof (parsed as { pr_url?: unknown }).pr_url === "string"
    ) {
      return parsed as ApiWizardSeedOut;
    }
  } catch {
    // Tampered cache — ignore and fetch from the API.
  }
  return null;
}
