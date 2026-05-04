/**
 * Server-side proxy for the wizard's Notion resource picker
 * (``GET /v1/workspaces/{ws}/knowledge/sources/notion/resources``).
 *
 * Same shape as ``api/knowledge/search/route.ts`` — keeps the bearer
 * token in the httpOnly session cookie instead of shipping it to the
 * browser.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listNotionResources,
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
  const q = url.searchParams.get("q") ?? undefined;
  const cursor = url.searchParams.get("cursor") ?? undefined;
  const typeParam = url.searchParams.get("type");
  const type =
    typeParam === "page" || typeParam === "database" || typeParam === "any"
      ? typeParam
      : undefined;

  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await listNotionResources(
      workspaceId,
      { integrationId, q, cursor, type },
      { token },
    );
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
