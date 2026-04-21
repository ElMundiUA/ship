/**
 * Per-repo wizard seed PR — wizard v2 step 4 (configure).
 *
 * Posts to the backend's unified seed endpoint
 * (``POST /v1/workspaces/{ws}/repos/{repo}/wizard_seed``) which:
 *
 *  1. Mints (or reuses) the repo's ``SHIP_RUN_TOKEN`` and pushes it
 *     to GitHub Actions secrets.
 *  2. Opens a single PR carrying preset workflows, ``.ship/config.yml``,
 *     knowledge starters, and the tracker FSM doc.
 *
 * Returns JSON with the PR URL. The RepoCard component refreshes
 * its own row from the response; no redirect.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  KNOWLEDGE_STARTERS,
  TRACKER_KINDS,
  isApiConfigured,
  wizardSeed,
  type KnowledgeStarterSlug,
  type TrackerKind,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

const KNOWN_PRESETS = new Set([
  "web-app",
  "api-backend",
  "mobile-app",
  "cli",
  "monorepo",
  "marketing",
  "adoption-minimum",
]);

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return json({ error: "api_unavailable" }, 503);
  }

  const body = (await request.json().catch(() => null)) as
    | {
        workspace_id?: string;
        repo_id?: string;
        presets?: string[];
        knowledge_slugs?: string[] | null;
        tracker_kind?: string | null;
        rotate_run_token?: boolean;
      }
    | null;

  if (!body?.workspace_id || !body.repo_id) {
    return json({ error: "bad_request" }, 400);
  }

  const presets = Array.isArray(body.presets)
    ? body.presets.filter((p) => KNOWN_PRESETS.has(p))
    : undefined;

  // ``null`` means "seed every starter"; ``[]`` means "skip knowledge".
  // Anything else we filter through the whitelist before forwarding.
  let knowledgeSlugs: KnowledgeStarterSlug[] | null | undefined;
  if (body.knowledge_slugs === null) {
    knowledgeSlugs = null;
  } else if (Array.isArray(body.knowledge_slugs)) {
    const allowed = new Set<string>(KNOWLEDGE_STARTERS);
    knowledgeSlugs = body.knowledge_slugs.filter((s) =>
      allowed.has(s),
    ) as KnowledgeStarterSlug[];
  } else {
    knowledgeSlugs = undefined;
  }

  const trackerAllowed = new Set<string>(TRACKER_KINDS);
  const trackerKind: TrackerKind | null | undefined =
    body.tracker_kind == null
      ? body.tracker_kind
      : trackerAllowed.has(body.tracker_kind)
        ? (body.tracker_kind as TrackerKind)
        : undefined;

  const token = (await getSessionToken()) ?? undefined;
  try {
    const result = await wizardSeed(
      body.workspace_id,
      body.repo_id,
      {
        presets,
        knowledge_slugs: knowledgeSlugs,
        tracker_kind: trackerKind,
        rotate_run_token: body.rotate_run_token === true,
      },
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
      // Extract the backend's structured code for a cleaner UI hint.
      const detail = err.detail as { code?: string } | null;
      const code = detail?.code ?? "precondition_failed";
      return json({ error: code }, 412);
    }
    if (err.status === 422) return json({ error: "bad_body" }, 422);
    if (err.status === 502) return json({ error: "github_api_error" }, 502);
    return json({ error: `http_${err.status}` }, err.status);
  }
  return json({ error: "unknown" }, 500);
}
