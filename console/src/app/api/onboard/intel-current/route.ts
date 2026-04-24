/**
 * Live :class:`RepoIntel` snapshot read-back — P5-09 polling badge.
 *
 * Proxies ``GET /v1/workspaces/{ws}/repos/{repo}/intel/current``
 * for the post-onboarding done page's intel-poll loop. The polling
 * happens client-side (5s interval, 3-min cap); we hop through this
 * route handler because the api client is server-only.
 *
 * 404 from the backend (= no harvest landed yet) bubbles up as
 * ``{"error": "not_found"}`` with status 404; the FE treats that
 * as "still harvesting" rather than a real error and keeps polling.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  getCurrentRepoIntel,
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
    const intel = await getCurrentRepoIntel(wsId, repoId, { token });
    return json({ intel }, 200);
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
