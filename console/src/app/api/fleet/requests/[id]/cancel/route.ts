/**
 * Browser-side proxy for fleet-request cancellation
 * (``POST /v1/workspaces/{ws}/fleet/requests/{id}/cancel``).
 *
 * Cancel flips the parent + every still-running child into
 * ``cancel_requested`` so the Console can dim the affected runs
 * immediately. Actual GitHub Actions cancellation lands in a
 * follow-up — the DB transition is honoured optimistically.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  cancelFleetRequest,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Body = { workspaceId: string };

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const { id } = await params;
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

  try {
    const token = (await getSessionToken()) ?? undefined;
    const result = await cancelFleetRequest(body.workspaceId, id, token);
    return NextResponse.json(result, { status: 200 });
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
