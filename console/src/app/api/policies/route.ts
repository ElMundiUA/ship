/**
 * Browser-side proxy for creating a workspace policy
 * (``POST /v1/workspaces/{ws}/policies``).
 *
 * Admin-only on the backend; this route just passes the session
 * bearer through and normalises the error envelope so the client
 * doesn't need to know about ``ApiHttpError``.
 */

import { NextResponse } from "next/server";

import {
  type ApiPolicyCreateIn,
  ApiHttpError,
  ApiUnavailableError,
  createPolicy,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = ApiPolicyCreateIn & { workspaceId: string };

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body.", code: "bad_request" },
      { status: 400 },
    );
  }

  if (!body.workspaceId) {
    return NextResponse.json(
      { error: "workspaceId is required.", code: "bad_request" },
      { status: 400 },
    );
  }
  if (!body.name || !body.pattern_id || !body.lane_id || !body.cadence) {
    return NextResponse.json(
      {
        error: "name, pattern_id, lane_id and cadence are all required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const payload: ApiPolicyCreateIn = {
      name: body.name,
      pattern_id: body.pattern_id,
      lane_id: body.lane_id,
      cadence: body.cadence,
      agent_slug: body.agent_slug,
      inputs: body.inputs,
      enabled: body.enabled,
    };
    const result = await createPolicy(body.workspaceId, payload, token);
    return NextResponse.json(result, { status: 201 });
  } catch (err) {
    return errorResponse(err);
  }
}

function errorResponse(err: unknown): NextResponse {
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
