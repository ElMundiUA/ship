/**
 * POST /api/template-update/activate — second click of the workspace-
 * home "Update template" flow. Operator already saw the PR open; this
 * route either merges it (action=merge) or just clears the pending
 * banner (action=skip) so they can merge by hand.
 *
 * Fields (FormData):
 *   ws        — workspace id
 *   repo_id   — repo whose seed PR we're acting on
 *   pr_number — PR number returned by /api/template-update/seed
 *   action    — "merge" | "skip"
 *   ws_scope  — optional ?ws=… scope to preserve
 */

import { NextResponse } from "next/server";

import {
  activateWizardSeed,
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getCachedSessionToken } from "@/lib/api/session-cache.server";


export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const ws = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const prNumberRaw = (form.get("pr_number") ?? "").toString();
  const action = (form.get("action") ?? "").toString();
  const wsScope = (form.get("ws_scope") ?? "").toString();

  if (action === "skip") {
    return clearAndRedirect(origin, wsScope);
  }
  if (action !== "merge") return back(origin, "bad_input", wsScope);
  if (!ws || !repoId) return back(origin, "bad_input", wsScope);
  const prNumber = Number.parseInt(prNumberRaw, 10);
  if (!Number.isFinite(prNumber) || prNumber <= 0) {
    return back(origin, "bad_input", wsScope);
  }
  if (!isApiConfigured()) return back(origin, "api_unavailable", wsScope);

  const token = await getCachedSessionToken();
  if (!token) {
    return NextResponse.redirect(
      new URL("/login?next=%2F&reason=session_expired", origin),
      303,
    );
  }

  try {
    await activateWizardSeed(ws, repoId, prNumber, token);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?next=%2F&reason=session_expired", origin),
        303,
      );
    }
    return back(origin, codeFor(err), wsScope);
  }

  const url = new URL("/", origin);
  url.searchParams.set("seed_merged", String(prNumber));
  if (wsScope) url.searchParams.set("ws", wsScope);
  return NextResponse.redirect(url, 303);
}


function back(origin: string, code: string, wsScope: string) {
  const url = new URL("/", origin);
  url.searchParams.set("seed_error", code);
  if (wsScope) url.searchParams.set("ws", wsScope);
  return NextResponse.redirect(url, 303);
}

function clearAndRedirect(origin: string, wsScope: string) {
  const url = new URL("/", origin);
  if (wsScope) url.searchParams.set("ws", wsScope);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 409) return "merge_blocked";
    if (err.status === 412) return "github_app_missing";
    if (err.status === 502) return "github_upstream_error";
    return `http_${err.status}`;
  }
  return "unknown";
}
