/**
 * Proxy for the custom-lane authoring flow (``/lanes?tab=new``).
 *
 * Accepts JSON from ``custom-author.tsx`` and forwards to the backend
 * ``POST /v1/workspaces/{ws}/repos/{repo}/lanes/propose``. Kept as an
 * app-router API route (rather than going direct from the browser)
 * so the session cookie is available and the backend URL never leaks
 * into client bundles.
 *
 * Error shaping mirrors the backend:
 * - ``409 sha_mismatch`` — ``.ship/config.yml`` moved; the author
 *   sees a banner and is asked to reload.
 * - ``409 lane_exists`` — the chosen slug collides with an existing
 *   lane. The client pre-checks this but a race is still possible.
 * - ``422 invalid_lane_id`` — slug failed the regex check.
 * - ``502 propose_failed`` — upstream GitHub error; rendered verbatim.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  proposeCustomLane,
} from "@/lib/api/client";

type ProposeRequestBody = {
  workspaceId: string;
  repoId: string;
  lane_id: string;
  agent_slug: string;
  schedule: string;
  prompt: string;
  base_sha: string | null;
  change_summary?: string;
};

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: ProposeRequestBody;
  try {
    body = (await request.json()) as ProposeRequestBody;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body.", code: "bad_request" },
      { status: 400 },
    );
  }

  if (!body.workspaceId || !body.repoId) {
    return NextResponse.json(
      { error: "workspaceId and repoId are required.", code: "bad_request" },
      { status: 400 },
    );
  }
  if (!body.lane_id || !body.agent_slug || !body.schedule || !body.prompt) {
    return NextResponse.json(
      {
        error: "lane_id, agent_slug, schedule and prompt are required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const result = await proposeCustomLane(body.workspaceId, body.repoId, {
      lane_id: body.lane_id,
      agent_slug: body.agent_slug,
      schedule: body.schedule,
      prompt: body.prompt,
      base_sha: body.base_sha,
      change_summary: body.change_summary,
    });
    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error: "Backend is unreachable.", code: "api_unavailable" },
        { status: 503 },
      );
    }
    if (err instanceof ApiHttpError) {
      const detail =
        err.detail && typeof err.detail === "object"
          ? (err.detail as Record<string, unknown>)
          : null;
      const code =
        detail && typeof detail.code === "string" ? detail.code : undefined;
      const message =
        detail && typeof detail.message === "string"
          ? detail.message
          : typeof err.detail === "string"
            ? err.detail
            : `HTTP ${err.status}`;
      return NextResponse.json(
        { error: message, code, detail },
        { status: err.status },
      );
    }
    return NextResponse.json(
      {
        error: err instanceof Error ? err.message : "Unknown error",
        code: "unknown",
      },
      { status: 500 },
    );
  }
}
