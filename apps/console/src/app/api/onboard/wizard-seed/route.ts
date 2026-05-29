/**
 * Per-repo wizard seed PR — wizard v2 confirm step (Wave-8c rewrite).
 *
 * Posts to the backend's unified seed endpoint
 * (``POST /v1/workspaces/{ws}/repos/{repo}/wizard_seed``) which:
 *
 *  1. Mints (or reuses) the repo's ``SHIP_RUN_TOKEN`` and pushes it
 *     to GitHub Actions secrets.
 *  2. Opens a single PR carrying the canonical Plays bundle
 *     (``DEFAULT_BUNDLE``), ``.ship/config.yml``, knowledge starters,
 *     and the tracker FSM doc.
 *  3. Pre-seeds Inbox routing rules from the repo's CODEOWNERS file.
 *  4. Dispatches a one-shot repo-intel harvest.
 *
 * Returns the full ``ApiWizardSeedResult`` (including ``codeowners``,
 * ``intel`` and ``synthetic_lanes_created`` from P5-06 / P5-07) so the
 * confirm step can stash it in sessionStorage for the done step to
 * render. The preset whitelist + knowledge_slugs forwarding lived
 * here pre-Wave-8c; it's gone because the backend now ignores both
 * (every run installs the canonical bundle).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  wizardSeed,
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
        rotate_run_token?: boolean;
      }
    | null;

  if (!body?.workspace_id || !body.repo_id) {
    return json({ error: "bad_request" }, 400);
  }

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await wizardSeed(
      body.workspace_id,
      body.repo_id,
      { rotate_run_token: body.rotate_run_token === true },
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
    if (err.status === 412) {
      const detail = err.detail as { code?: string } | null;
      const code = detail?.code ?? "precondition_failed";
      return json({ error: code }, 412);
    }
    if (err.status === 422) return json({ error: "bad_body" }, 422);
    if (err.status === 502) {
      const detail = err.detail as { code?: string; message?: string; upstream_status?: number } | null;
      return json({
        error: "github_api_error",
        detail: detail?.message ?? null,
        upstream_status: detail?.upstream_status ?? null,
      }, 502);
    }
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
