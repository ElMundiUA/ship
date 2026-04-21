/**
 * Per-repo tracker binding — wizard v2 step 4 (configure).
 *
 * Unlike the step 1/2 onboarding handlers which 303-redirect, this
 * handler returns JSON because the RepoCard client component calls
 * it with ``fetch`` and updates its local state from the response.
 * No redirect means refresh semantics stay with the caller.
 *
 * Two verbs:
 *
 * - ``POST`` — upsert the per-repo binding with a tracker kind +
 *   optional config (team id / project key).
 * - ``DELETE`` — drop the per-repo binding; the repo falls back to
 *   the workspace default.
 *
 * Both require the session cookie and an admin role on the workspace
 * (enforced server-side via the backend; we only relay the 403).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  TRACKER_KINDS,
  deleteRepoTrackerBinding,
  isApiConfigured,
  setRepoTrackerBinding,
  type TrackerKind,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const body = (await request.json().catch(() => null)) as
    | {
        workspace_id?: string;
        repo_id?: string;
        kind?: string;
        config?: Record<string, unknown>;
      }
    | null;

  if (!body?.workspace_id || !body.repo_id || !body.kind) {
    return json({ error: "bad_request" }, 400);
  }

  const allowed = new Set<string>(TRACKER_KINDS);
  if (!allowed.has(body.kind)) {
    return json({ error: "bad_kind" }, 422);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await setRepoTrackerBinding(
      body.workspace_id,
      body.repo_id,
      { kind: body.kind as TrackerKind, config: body.config ?? {} },
      token,
    );
    return json({ binding: result }, 200);
  } catch (err) {
    return relayError(err);
  }
}

export async function DELETE(request: Request) {
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
    await deleteRepoTrackerBinding(wsId, repoId, token);
    return json({ ok: true }, 200);
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
    if (err.status === 409) return json({ error: "conflict" }, 409);
    if (err.status === 422) return json({ error: "bad_body" }, 422);
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
