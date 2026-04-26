/**
 * Server-side proxy for the workspace knowledge vector-search endpoint
 * (``POST /v1/workspaces/{ws}/knowledge/search``), PR-7A.
 *
 * Kept in Next.js so the session bearer never leaves the httpOnly
 * cookie; the ``/fleet/knowledge`` Search tab posts here.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  searchKnowledge,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = {
  workspaceId: string;
  query: string;
  repoId?: string | null;
  bucketSlug?: string | null;
  limit?: number;
};

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
  const query = (body.query ?? "").trim();
  if (query.length < 1) {
    return NextResponse.json(
      { error: "query must be non-empty.", code: "bad_request" },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const resp = await searchKnowledge(
      body.workspaceId,
      {
        query,
        repoId: body.repoId ?? null,
        bucketSlug: body.bucketSlug ?? null,
        limit: body.limit,
      },
      token,
    );
    return NextResponse.json(resp, { status: 200 });
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
