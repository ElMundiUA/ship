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
      if (err.status === 502) {
        const { reason, detail } = classifyUpstream(err.detail);
        return back(origin, wsId, pipelineId, reason, detail);
      }
      return back(origin, wsId, pipelineId, `http_${err.status}`);
    }
    return back(origin, wsId, pipelineId, "unknown");
  }
}

/**
 * Map the backend's 502 ``{code, upstream_status, message}`` detail to
 * a concrete banner reason. Specifically flags the three most common
 * root causes so the user doesn't have to read a raw GitHub API body
 * to figure out what to fix:
 *
 * - Missing GitHub App ``workflows`` permission (403 + "Resource not
 *   accessible by integration" + the committed path starts with
 *   ``.github/workflows/``). By far the #1 cause on pilot tenants.
 * - Missing ``contents`` or ``pull_requests`` scope (same 403 body,
 *   different API surface).
 * - Repo not in the App's "selected repositories" list (404 on the
 *   git/ref call).
 *
 * Anything else falls through to ``install_upstream`` with the
 * upstream status + message snippet as ``detail`` so we at least
 * surface something actionable in the banner.
 */
function classifyUpstream(
  detail: unknown,
): { reason: string; detail?: string } {
  if (!detail || typeof detail !== "object") return { reason: "install_upstream" };
  const d = detail as {
    upstream_status?: unknown;
    message?: unknown;
  };
  const status = typeof d.upstream_status === "number" ? d.upstream_status : 0;
  const message = typeof d.message === "string" ? d.message : "";
  const lower = message.toLowerCase();
  const snippet = message
    ? `GitHub ${status || "?"}: ${message.slice(0, 240)}`
    : undefined;

  if (status === 403 && lower.includes("resource not accessible by integration")) {
    // Body excerpt doesn't tell us *which* scope is missing — the
    // caller always hits Contents (PUT workflows file) first, so the
    // workflows-permission case dominates in practice.
    return { reason: "install_upstream_workflows_scope", detail: snippet };
  }
  if (status === 404) {
    return { reason: "install_upstream_repo_not_selected", detail: snippet };
  }
  return { reason: "install_upstream", detail: snippet };
}

function back(
  origin: string,
  wsId: string,
  pipelineId: string,
  reason: string,
  detail?: string,
) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("installed", pipelineId);
  url.searchParams.set("reason", reason);
  if (detail) url.searchParams.set("detail", detail);
  return NextResponse.redirect(url, 303);
}
