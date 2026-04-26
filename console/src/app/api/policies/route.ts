/**
 * Browser-side proxy for creating a workspace prose-rule policy
 * (``POST /v1/workspaces/{ws}/policies``).
 *
 * Passes the session bearer through and normalises the error envelope
 * so the client doesn't need to know about ``ApiHttpError``.
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
  if (!body.title?.trim() || !body.body?.trim()) {
    return NextResponse.json(
      {
        error: "title and body are both required.",
        code: "bad_request",
      },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const payload: ApiPolicyCreateIn = {
      title: body.title,
      body: body.body,
      enabled: body.enabled,
      sort_order: body.sort_order,
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
