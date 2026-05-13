/**
 * Onboarding step — kick off the GitHub App install flow.
 *
 * The user clicks "Install Ship on GitHub" on the wizard or the
 * integrations page. We:
 *
 * 1. Ask the backend for a signed install URL bound to ``ws``.
 * 2. 303-redirect the browser straight to that URL on github.com.
 * 3. GitHub bounces the user back to ``GET /v1/integrations/github/install/
 *    callback`` on the API origin once they pick repos; the backend then
 *    redirects them back into ``/onboarding?step=tracker&github=installed``
 *    on the configured console origin (``SHIP_CONSOLE_URL``).
 *
 * No state needs to be persisted on the console — the signed state token
 * lives in the URL all the way back to the API. We don't even need to hold
 * the session through the round-trip because the install URL is a public
 * github.com page; auth re-attaches on the callback's redirect.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  startGitHubAppInstall,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();

  if (!wsId) {
    // No workspace context → kick the user back to the wizard's first
    // step. They probably refreshed mid-flow.
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  try {
    const start = await startGitHubAppInstall(wsId);
    return NextResponse.redirect(start.install_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403)
        return wizardError(origin, wsId, "forbidden");
      // 404 = ``_require_membership`` hides non-workspaces from strangers;
      // also triggers when ``?ws=`` is stale or the user switched accounts.
      if (err.status === 404)
        return wizardError(origin, wsId, "workspace_not_found");
      // 503 = backend says the App is not configured (missing
      // GITHUB_APP_* env vars); surface a dedicated error so ops sees it.
      if (err.status === 503)
        return wizardError(origin, wsId, "app_not_configured");
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }
}

function wizardError(origin: string, wsId: string, code: string) {
  // Stay on the github step so the error banner sits next to the same
  // "Install on GitHub" button the user just clicked — much less
  // confusing than punting them to a different step.
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "github");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
