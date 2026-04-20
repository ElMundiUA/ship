/**
 * Dashboard form handler — open the install-workflow PR for a pipeline.
 *
 * Posted from the "Install workflow" CTA on a pipeline card whose
 * starter workflow file isn't yet present in the customer repo. We
 * forward the call to the backend (which talks to the GitHub App's
 * contents:write permission to open the PR) and bounce back to the
 * dashboard. On success the response carries the new PR URL — we
 * stuff it into a query string so the next render can render a
 * "Review the PR →" pill, and (for browsers without JS) we also
 * 303 straight to the PR so the user doesn't lose the link.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  installPipelineWorkflow,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const pipelineId = (form.get("pipeline") ?? "").toString();
  // Explicit repo context: the card was fired from a specific repo's
  // swimlane, so the backend rebinds the pipeline to that repo rather
  // than running the sole-repo auto-bind heuristic. Harmless when
  // omitted — the backend still falls back to the existing binding.
  const repoIdRaw = (form.get("repo_id") ?? "").toString().trim();
  const repoId = repoIdRaw || null;

  if (!wsId || !pipelineId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, pipelineId, "api_unavailable");
  }

  try {
    const result = await installPipelineWorkflow(wsId, pipelineId, { repoId });
    // Belt-and-braces: redirect straight to the PR so the user lands
    // on github.com to review and merge — the dashboard surfaces the
    // banner on the next manual refresh.
    if (result.pr_url) {
      return NextResponse.redirect(result.pr_url, 303);
    }
    return back(origin, wsId, pipelineId, "installed");
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, pipelineId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403)
        return back(origin, wsId, pipelineId, "forbidden");
      if (err.status === 404)
        return back(origin, wsId, pipelineId, "missing");
      if (err.status === 412) {
        const code =
          err.detail && typeof err.detail === "object" && "code" in err.detail
            ? String((err.detail as { code: unknown }).code)
            : "precondition";
        return back(origin, wsId, pipelineId, `install_${code}`);
      }
      if (err.status === 502)
        return back(origin, wsId, pipelineId, "install_upstream");
      return back(origin, wsId, pipelineId, `http_${err.status}`);
    }
    return back(origin, wsId, pipelineId, "unknown");
  }
}

function back(origin: string, wsId: string, pipelineId: string, reason: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("installed", pipelineId);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}
