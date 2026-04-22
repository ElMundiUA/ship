/**
 * Browser-side proxy for deleting a workspace-private pattern
 * (``DELETE /v1/workspaces/{ws}/patterns/{id}``).
 *
 * Path ``[id]`` is the ``custom_patterns.id`` PK, not the textual
 * ``pattern_id``. ``workspaceId`` arrives as a query parameter so
 * browsers can issue a plain ``fetch(..., { method: "DELETE" })``
 * without a body.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteCustomPattern,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ id: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const { id } = await context.params;
  const url = new URL(request.url);
  const workspaceId = url.searchParams.get("workspaceId");
  if (!workspaceId) {
    return NextResponse.json(
      { error: "workspaceId query param is required.", code: "bad_request" },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    await deleteCustomPattern(workspaceId, id, token);
    return NextResponse.json(null, { status: 204 });
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
