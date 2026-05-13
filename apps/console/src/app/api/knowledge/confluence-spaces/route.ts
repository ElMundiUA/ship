/**
 * Server-side proxy for the Confluence space-picker
 * (``GET /v1/workspaces/{ws}/knowledge/sources/confluence/spaces``).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listConfluenceSpaces,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const url = new URL(request.url);
  const workspaceId = url.searchParams.get("workspaceId");
  const integrationId = url.searchParams.get("integrationId");
  if (!workspaceId || !integrationId) {
    return NextResponse.json(
      { error: "workspaceId and integrationId are required.", code: "bad_request" },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await listConfluenceSpaces(workspaceId, { integrationId }, { token });
    return NextResponse.json(data, { status: 200 });
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
    const message =
      detail && typeof detail.message === "string"
        ? detail.message
        : typeof err.detail === "string"
          ? err.detail
          : `HTTP ${err.status}`;
    return NextResponse.json({ error: message, detail }, { status: err.status });
  }
  return NextResponse.json(
    { error: err instanceof Error ? err.message : "Unknown error", code: "unknown" },
    { status: 500 },
  );
}
