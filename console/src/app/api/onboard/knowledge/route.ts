/**
 * Onboarding step — seed starter knowledge buckets (one-shot PR).
 *
 * Form submits one of two intents:
 *
 * - **Skip** (`intent=skip`) — no API call, just advance to `step=done`.
 *   Users who don't want Ship to open a PR today can flip starter
 *   buckets on later from the knowledge page in the console.
 *
 * - **Seed** (`intent=seed`) — resolve the user's activated sandbox
 *   repo (the first row from `/v1/workspaces/{ws}/repos`, which is
 *   ordered by `activated_at`) and POST to
 *   `/v1/workspaces/{ws}/repos/{id}/knowledge_seed` with the ticked
 *   slugs. The backend opens a PR in the tenant repo dropping
 *   `.ship/knowledge/<slug>.md`. We forward to `step=done` with a
 *   flash param (`knowledge=seeded`) so the done card can surface
 *   the PR link; on seed failure we re-render the knowledge step
 *   with an error banner so the user can retry or skip.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  KNOWLEDGE_STARTERS,
  isApiConfigured,
  knowledgeSeed,
  listActivatedRepos,
  type KnowledgeStarterSlug,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const intent = (form.get("intent") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (intent === "skip") {
    return forwardDone(origin, wsId);
  }

  if (intent !== "seed") {
    return wizardError(origin, wsId, "bad_intent");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  const allowed = new Set<string>(KNOWLEDGE_STARTERS);
  const selection = form
    .getAll("slug")
    .map((v) => v.toString().trim())
    .filter((s) => allowed.has(s)) as KnowledgeStarterSlug[];

  if (selection.length === 0) {
    return wizardError(origin, wsId, "empty_selection");
  }

  const token = (await getSessionToken()) ?? undefined;

  let repoId: string | null = null;
  try {
    const activated = await listActivatedRepos(wsId, token);
    // Onboarding assumption: the user either just activated exactly one
    // repo (the sandbox they wired in step 2) or their workspace already
    // had one. If there are multiple, we pick the most recently
    // activated so the wizard consistently seeds the newest sandbox
    // rather than an older production repo.
    const sorted = [...activated].sort((a, b) => {
      const ax = a.activated_at ? Date.parse(a.activated_at) : 0;
      const bx = b.activated_at ? Date.parse(b.activated_at) : 0;
      return bx - ax;
    });
    repoId = sorted[0]?.id ?? null;
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return wizardError(origin, wsId, "api_unavailable");
    }
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return wizardError(origin, wsId, "repo_lookup_failed");
  }

  if (!repoId) {
    return wizardError(origin, wsId, "no_repo");
  }

  try {
    const result = await knowledgeSeed(wsId, repoId, {
      selection,
      token,
    });
    const next = new URL("/onboarding", origin);
    next.searchParams.set("step", "done");
    next.searchParams.set("ws", wsId);
    next.searchParams.set("knowledge", "seeded");
    next.searchParams.set("pr", String(result.pr_number));
    next.searchParams.set("pr_url", result.pr_url);
    return NextResponse.redirect(next, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return wizardError(origin, wsId, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 403) return wizardError(origin, wsId, "forbidden");
      if (err.status === 412) return wizardError(origin, wsId, "github_app_missing");
      if (err.status === 422) return wizardError(origin, wsId, "bad_selection");
      if (err.status === 502) return wizardError(origin, wsId, "github_api_error");
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }
}

function forwardDone(origin: string, wsId: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "done");
  url.searchParams.set("ws", wsId);
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, code: string) {
  // Wizard v2 collapsed the knowledge step into the per-repo configure
  // card (the unified seed PR carries starter markdown). Legacy callers
  // still reach this route — if they 5xx, send them to the configure
  // step where they can retry via the per-repo "Open seed PR" button.
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "configure");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
