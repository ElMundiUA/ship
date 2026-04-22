/**
 * Browser-side proxy for persisting a workspace-private pattern
 * (``POST /v1/workspaces/{ws}/patterns``).
 *
 * Admin-only on the backend. The AI author modal hits this route
 * after the operator has reviewed/edited the draft returned from
 * ``/api/patterns/draft``; hand-crafted (non-LLM) saves also land
 * here.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createCustomPattern,
  isApiConfigured,
  type ApiCustomPatternIn,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = ApiCustomPatternIn & { workspaceId: string };

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
  if (!body.pattern_id || !body.name || !body.modes?.length) {
    return NextResponse.json(
      {
        error: "pattern_id, name, and at least one mode are required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const payload: ApiCustomPatternIn = {
      pattern_id: body.pattern_id,
      name: body.name,
      description: body.description ?? "",
      category: body.category ?? null,
      modes: body.modes,
      inputs: body.inputs ?? [],
      spec: body.spec ?? {},
      body: body.body ?? "",
    };
    const created = await createCustomPattern(
      body.workspaceId,
      payload,
      token,
    );
    return NextResponse.json(created, { status: 201 });
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
