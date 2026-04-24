/**
 * Latest wizard seed result read-back — P5-09 done page fallback.
 *
 * Proxies ``GET /v1/workspaces/{ws}/repos/{repo}/wizard_seed/latest``
 * for the post-onboarding "What just happened" page when the
 * sessionStorage cache (``ship.wizard_seed_result.<repo_id>``) is
 * empty. The route exists because the api client is server-only and
 * the done-page card is a client component (sessionStorage / polling).
 *
 * 404 from the backend (= no wizard run for this repo) bubbles up as
 * ``{"error": "not_found"}`` with status 404; the FE treats that as
 * the "no bootstrap yet" empty state, not an error.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  getLatestWizardSeed,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const url = new URL(request.url);
  const wsId = url.searchParams.get("workspace_id");
  const repoId = url.searchParams.get("repo_id");
  if (!wsId || !repoId) {
    return json({ error: "bad_request" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await getLatestWizardSeed(wsId, repoId, token);
    return json({ result }, 200);
  } catch (err) {
    return relayError(err);
  }
}

function json(body: unknown, status: number) {
  return NextResponse.json(body, { status });
}

function relayError(err: unknown) {
  if (err instanceof ApiUnavailableError) return json({ error: "api_unavailable" }, 502);
  if (err instanceof ApiHttpError) {
    if (err.status === 401) return json({ error: "session_expired" }, 401);
    if (err.status === 403) return json({ error: "forbidden" }, 403);
    if (err.status === 404) return json({ error: "not_found" }, 404);
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
