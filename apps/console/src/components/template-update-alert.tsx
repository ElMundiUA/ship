"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import type { ApiActivatedRepo } from "@/lib/api/client";

/**
 * Two-click "Update Ship template" alert. Lives in the dashboard
 * StatusAlerts strip. Drives ``POST /api/template-update/seed`` →
 * ``POST /api/template-update/activate`` without a full-page reload —
 * uses ``router.refresh()`` after each mutation so the server
 * re-renders the dashboard with the fresh ``priorities`` /
 * ``inboxItems`` / etc. but the layout, scroll position, and other
 * client state stay put.
 *
 * State machine:
 *
 *   idle       — "Update available · Update →"
 *   seeding    — disabled spinner ("Opening PR…")
 *   pending    — "PR #N opened · auto-merge when CI passes? [Yes] [I'll merge myself]"
 *   activating — disabled spinner ("Merging…") inside pending row
 *   merged     — "Updated · PR #N merged. Bundle live on next routine tick. [Dismiss]"
 *   error      — coral inline banner with a context message + [Dismiss]
 */

type Stage = "idle" | "seeding" | "pending" | "activating" | "merged" | "error";

const ERROR_MESSAGES: Record<string, string> = {
  merge_blocked:
    "PR opened but GitHub wouldn't merge — branch protection or required checks aren't satisfied. Open the PR on GitHub to finish.",
  github_app_missing:
    "Ship's GitHub App isn't installed. Reinstall it and try again.",
  github_upstream_error:
    "GitHub rejected the merge. Open the PR and merge by hand.",
  validation_failed:
    "The seed call failed validation. Refresh and try again.",
  forbidden: "You don't have permission to update the template here.",
  not_found: "Repo or workspace went missing. Refresh and try again.",
  api_unavailable: "Ship API is unreachable. Try again in a moment.",
  bad_input:
    "The action couldn't be applied — required fields were missing.",
};


export function TemplateUpdateAlert({
  workspaceId,
  reposNeedingUpdate,
  multiWs,
}: {
  workspaceId: string;
  reposNeedingUpdate: ApiActivatedRepo[];
  multiWs: boolean;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [stage, setStage] = useState<Stage>("idle");
  const [prNumber, setPrNumber] = useState<number | null>(null);
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  const repo = reposNeedingUpdate[0];
  if (!repo) return null;

  const wsScope = multiWs ? workspaceId : "";

  async function onSeed() {
    setStage("seeding");
    setErrorCode(null);
    const fd = new FormData();
    fd.set("ws", workspaceId);
    fd.set("repo_id", repo.id);
    fd.set("ws_scope", wsScope);
    fd.set("nojs", "1"); // signal the route to return JSON instead of redirect
    try {
      const res = await fetch("/api/template-update/seed", {
        method: "POST",
        body: fd,
        // Don't follow the redirect — we want JSON back. The server
        // route still 303s for the no-JS path; we read the redirect
        // target's query params client-side.
        redirect: "manual",
      });
      const target = parseRedirect(res);
      if (target.kind === "ok") {
        setPrNumber(target.seedPr);
        setPrUrl(target.prUrl);
        setStage("pending");
        // Refresh server data (priorities, inbox, etc.) without a full
        // reload so the rest of the dashboard reflects the new state.
        startTransition(() => router.refresh());
      } else {
        setErrorCode(target.code);
        setStage("error");
      }
    } catch {
      setErrorCode("api_unavailable");
      setStage("error");
    }
  }

  async function onActivate(action: "merge" | "skip") {
    if (action === "skip") {
      setStage("idle");
      setPrNumber(null);
      startTransition(() => router.refresh());
      return;
    }
    if (prNumber === null) return;
    setStage("activating");
    setErrorCode(null);
    const fd = new FormData();
    fd.set("ws", workspaceId);
    fd.set("repo_id", repo.id);
    fd.set("pr_number", String(prNumber));
    fd.set("action", "merge");
    fd.set("ws_scope", wsScope);
    try {
      const res = await fetch("/api/template-update/activate", {
        method: "POST",
        body: fd,
        redirect: "manual",
      });
      const target = parseRedirect(res);
      if (target.kind === "ok" && target.seedMerged !== null) {
        setStage("merged");
        startTransition(() => router.refresh());
      } else if (target.kind === "ok") {
        // No seed_merged flag → operator chose skip somehow; treat as idle.
        setStage("idle");
      } else {
        setErrorCode(target.code);
        setStage("error");
      }
    } catch {
      setErrorCode("github_upstream_error");
      setStage("error");
    }
  }

  function dismiss() {
    setStage("idle");
    setPrNumber(null);
    setPrUrl(null);
    setErrorCode(null);
  }

  if (stage === "merged") {
    return (
      <li className="flex items-baseline justify-between gap-3 py-3">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
            Ship template
          </p>
          <p className="mt-1 text-sm text-white/85">
            <span className="font-semibold text-white">Updated</span> · PR
            #{prNumber} merged. Bundle live on next routine tick.
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
        >
          Dismiss
        </button>
      </li>
    );
  }

  if (stage === "error") {
    return (
      <li className="flex items-baseline justify-between gap-3 py-3">
        <div className="min-w-0">
          <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-coral/85">
            Ship template
          </p>
          <p className="mt-1 text-sm text-coral/95">
            {errorCode ? errorMessageFor(errorCode) : "Couldn't update."}
          </p>
        </div>
        <button
          type="button"
          onClick={dismiss}
          className="shrink-0 text-xs font-semibold text-white/55 hover:text-white"
        >
          Dismiss
        </button>
      </li>
    );
  }

  if (stage === "pending" || stage === "activating") {
    return (
      <li className="py-3">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
          Ship template
        </p>
        <p className="mt-1 text-sm text-white/85">
          <span className="font-semibold text-white">
            PR #{prNumber} opened
          </span>{" "}
          ·{" "}
          {prUrl && (
            <a
              href={prUrl}
              target="_blank"
              rel="noreferrer"
              className="text-white/55 underline-offset-2 hover:text-white hover:underline"
            >
              view on GitHub
            </a>
          )}{" "}
          · auto-merge when CI passes?
        </p>
        <div className="mt-2 flex items-center gap-3">
          <button
            type="button"
            disabled={stage === "activating"}
            onClick={() => onActivate("merge")}
            className="inline-flex items-center gap-1.5 rounded-full bg-gradient-to-r from-coral via-lilac to-aqua px-3.5 py-1.5 text-xs font-bold text-ink shadow-glow transition hover:brightness-110 disabled:opacity-50"
          >
            {stage === "activating" ? "Merging…" : "Yes, auto-merge"}
          </button>
          <button
            type="button"
            disabled={stage === "activating"}
            onClick={() => onActivate("skip")}
            className="inline-flex items-center gap-1.5 rounded-full border border-white/15 bg-white/[0.04] px-3 py-1.5 text-[11px] font-semibold text-white/85 transition hover:bg-white/[0.08] disabled:opacity-50"
          >
            I&rsquo;ll merge it myself
          </button>
        </div>
      </li>
    );
  }

  // idle / seeding
  return (
    <li className="flex items-baseline justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-aqua/75">
          Ship template
        </p>
        <p className="mt-1 text-sm text-white/85">
          <span className="font-semibold text-white">Update available</span>{" "}
          ·{" "}
          {reposNeedingUpdate.length === 1
            ? reposNeedingUpdate[0].full_name
            : `${reposNeedingUpdate.length} repos behind`}
        </p>
      </div>
      <button
        type="button"
        disabled={stage === "seeding"}
        onClick={onSeed}
        className="shrink-0 text-xs font-semibold text-aqua hover:text-white disabled:opacity-50"
      >
        {stage === "seeding" ? "Opening PR…" : "Update →"}
      </button>
    </li>
  );
}


type RedirectParse =
  | { kind: "ok"; seedPr: number | null; seedMerged: number | null; prUrl: string | null }
  | { kind: "error"; code: string };

function parseRedirect(res: Response): RedirectParse {
  // Server route returns 303 with a Location header carrying the
  // result query params. With ``redirect: "manual"`` the browser
  // exposes status 0 / type "opaqueredirect" so we read Location
  // off the response. Note: same-origin only — Next.js routes here
  // always redirect to ``/``.
  const location = res.headers.get("location") || res.url || "";
  if (!location) {
    return { kind: "error", code: "bad_input" };
  }
  const url = new URL(location, "http://_");
  const seedError = url.searchParams.get("seed_error");
  if (seedError) return { kind: "error", code: seedError };
  const seedPr = parseIntOrNull(url.searchParams.get("seed_pr"));
  const seedMerged = parseIntOrNull(url.searchParams.get("seed_merged"));
  return { kind: "ok", seedPr, seedMerged, prUrl: null };
}

function parseIntOrNull(v: string | null): number | null {
  if (!v) return null;
  const n = Number.parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function errorMessageFor(code: string): string {
  return ERROR_MESSAGES[code] ?? `Couldn't update the template (${code}).`;
}
