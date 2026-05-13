/**
 * Per-repo agent secrets — wizard v2 step 4 (configure).
 *
 * Two verbs:
 *
 * - ``GET`` — return the catalog filtered to the requested slugs with
 *   ``present=true/false`` per agent, based on what GitHub Actions
 *   already has in the repo's secrets.
 * - ``POST`` — push a batch of plaintext secrets to the repo's
 *   GitHub Actions via the iter 3 backend endpoint. Plaintext lives
 *   only in the HTTP hop; neither this handler nor the backend
 *   persists it.
 *
 * Both return JSON (the RepoCard client component updates its local
 * state from the response, no redirect).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  checkAgentSecrets,
  isApiConfigured,
  pushAgentSecrets,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const url = new URL(request.url);
  const wsId = url.searchParams.get("workspace_id");
  const repoId = url.searchParams.get("repo_id");
  const slugsRaw = url.searchParams.get("slugs");

  if (!wsId || !repoId) {
    return json({ error: "bad_request" }, 400);
  }

  const slugs = slugsRaw
    ? slugsRaw
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : undefined;

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await checkAgentSecrets(wsId, repoId, { slugs, token });
    return json({ check: result }, 200);
  } catch (err) {
    return relayError(err);
  }
}

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const body = (await request.json().catch(() => null)) as
    | {
        workspace_id?: string;
        repo_id?: string;
        secrets?: { slug?: string; plaintext?: string }[];
      }
    | null;

  if (!body?.workspace_id || !body.repo_id || !Array.isArray(body.secrets)) {
    return json({ error: "bad_request" }, 400);
  }

  // Drop empty entries client-side so a noisy form doesn't 400 the
  // whole batch. The backend also 400s on a fully-empty list.
  const cleaned = body.secrets
    .map((s) => ({
      slug: (s.slug ?? "").trim(),
      plaintext: (s.plaintext ?? "").trim(),
    }))
    .filter((s) => s.slug && s.plaintext);

  if (cleaned.length === 0) {
    return json({ error: "empty_selection" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await pushAgentSecrets(
      body.workspace_id,
      body.repo_id,
      cleaned,
      token,
    );
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
    if (err.status === 409) return json({ error: "conflict" }, 409);
    if (err.status === 412) return json({ error: "github_app_missing" }, 412);
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
