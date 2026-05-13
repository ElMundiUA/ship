/**
 * POST /api/template-update/seed — open a Ship template-update PR
 * via the wizard seed endpoint, but skip the wizard preview screen.
 *
 * Fields (FormData):
 *   ws       — workspace id
 *   repo_id  — repo to seed
 *   ws_scope — optional ?ws=… scope to preserve in the redirect
 *
 * On success bounces back to ``/?seed_pr=<N>&seed_repo=<id>`` so the
 * StatusAlerts strip can render the "Auto-merge?" confirmation step.
 * On failure bounces with ``?seed_error=<code>`` for an inline banner.
 *
 * The companion ``activate`` route handles the second click (merge).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  wizardSeed,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getCachedSessionToken } from "@/lib/api/session-cache.server";


export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const ws = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const wsScope = (form.get("ws_scope") ?? "").toString();

  if (!ws || !repoId) return back(origin, "bad_input", wsScope);
  if (!isApiConfigured()) return back(origin, "api_unavailable", wsScope);

  const token = await getCachedSessionToken();
  if (!token) {
    return NextResponse.redirect(
      new URL("/login?next=%2F&reason=session_expired", origin),
      303,
    );
  }

  let result;
  try {
    result = await wizardSeed(
      ws,
      repoId,
      // Defaults the wizard would pass on a re-seed: server picks
      // DEFAULT_BUNDLE; FSM doc rendered if the repo has a tracker
      // binding; run-token NOT rotated unless the operator explicitly
      // asks (re-seeding shouldn't invalidate in-flight runners).
      { include_fsm: true, rotate_run_token: false },
      token,
    );
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
  url.searchParams.set("seed_pr", String(result.pr_number));
  url.searchParams.set("seed_repo", repoId);
  if (wsScope) url.searchParams.set("ws", wsScope);
  return NextResponse.redirect(url, 303);
}


function back(origin: string, code: string, wsScope: string) {
  const url = new URL("/", origin);
  url.searchParams.set("seed_error", code);
  if (wsScope) url.searchParams.set("ws", wsScope);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
