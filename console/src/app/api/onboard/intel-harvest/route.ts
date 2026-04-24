/**
 * Manual repo-intel harvest re-trigger — P5-09 retry button.
 *
 * Proxies ``POST /v1/workspaces/{ws}/repos/{repo}/intel/harvest``
 * which reuses the wizard's own dispatch helper server-side. Returns
 * the same handle shape as the wizard's :type:`ApiWizardSeedIntelHandle`
 * so the FE can fold the response back into its polling loop without
 * branching on inline-vs-queued execution.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  triggerRepoIntelHarvest,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const body = (await request.json().catch(() => null)) as
    | { workspace_id?: string; repo_id?: string }
    | null;

  if (!body?.workspace_id || !body.repo_id) {
    return json({ error: "bad_request" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const handle = await triggerRepoIntelHarvest(
      body.workspace_id,
      body.repo_id,
      token,
    );
    return json({ handle }, 200);
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
