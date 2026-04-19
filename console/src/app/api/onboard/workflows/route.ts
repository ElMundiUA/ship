/**
 * Onboarding step 3 — install Ship workflow artifacts into the user's repo.
 *
 * The wizard surfaces a checkbox per recommended workflow. Whichever boxes
 * are ticked get POSTed here under `workflow` (multiple values allowed).
 * We forward to `/v1/onboarding/install-workflows`, then advance to the
 * tracker step. "Skip" sends `intent=skip` and goes straight to tracker
 * without touching the API.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  installWorkflows,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repo = (form.get("repo") ?? "").toString();
  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }
  if (!repo) {
    // No repo means we have nothing to install into; skip ahead.
    return advance(origin, wsId, repo);
  }

  if (form.get("intent") === "skip") {
    return advance(origin, wsId, repo);
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, repo, "api_unavailable");
  }

  const workflowIds = form.getAll("workflow").map((v) => v.toString()).filter(Boolean);
  if (workflowIds.length === 0) {
    return wizardError(origin, wsId, repo, "missing_selection");
  }

  try {
    const result = await installWorkflows({
      workspace_id: wsId,
      repo_source: repo,
      workflow_ids: workflowIds,
    });
    const url = new URL("/onboarding", origin);
    url.searchParams.set("step", "tracker");
    url.searchParams.set("ws", wsId);
    url.searchParams.set("repo", repo);
    if (result.commit_made && result.head_after) {
      url.searchParams.set("commit", result.head_after.slice(0, 12));
    }
    url.searchParams.set("installed", result.installed.length.toString());
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return wizardError(origin, wsId, repo, "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      const code =
        typeof err.detail === "object" &&
        err.detail !== null &&
        "code" in err.detail
          ? String((err.detail as { code: unknown }).code)
          : `http_${err.status}`;
      return wizardError(origin, wsId, repo, code);
    }
    return wizardError(origin, wsId, repo, "unknown");
  }
}

function advance(origin: string, wsId: string, repo: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "tracker");
  url.searchParams.set("ws", wsId);
  if (repo) url.searchParams.set("repo", repo);
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, repo: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "workflows");
  url.searchParams.set("ws", wsId);
  if (repo) url.searchParams.set("repo", repo);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
