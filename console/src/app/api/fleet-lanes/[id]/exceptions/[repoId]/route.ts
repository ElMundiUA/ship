/**
 * Browser-side proxy for toggling a per-repo Fleet-lane exception
 * (``POST`` adds/updates, ``DELETE`` removes).
 *
 * Both verbs return the full updated ``ApiFleetLane`` so the
 * client can swap the in-memory row without a refetch.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  addFleetLaneException,
  isApiConfigured,
  removeFleetLaneException,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type Params = Promise<{ id: string; repoId: string }>;

export async function POST(
  request: Request,
  { params }: { params: Params },
) {
  if (!isApiConfigured()) return unavailable();
  const { id, repoId } = await params;

  let body: { workspaceId?: string; reason?: string | null } = {};
  try {
    body = await request.json();
  } catch {
    /* empty body is fine */
  }
  if (!body.workspaceId) {
    return NextResponse.json(
      { error: "workspaceId is required.", code: "bad_request" },
      { status: 400 },
    );
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const result = await addFleetLaneException(
      body.workspaceId,
      id,
      repoId,
      { reason: body.reason ?? null },
      token,
    );
    return NextResponse.json(result);
  } catch (err) {
    return errorResponse(err);
  }
}

export async function DELETE(
  request: Request,
  { params }: { params: Params },
) {
  if (!isApiConfigured()) return unavailable();
  const { id, repoId } = await params;

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
    const result = await removeFleetLaneException(
      workspaceId,
      id,
      repoId,
      token,
    );
    return NextResponse.json(result);
  } catch (err) {
    return errorResponse(err);
  }
}

function unavailable() {
  return NextResponse.json(
    { error: "Backend is not configured.", code: "api_unavailable" },
    { status: 503 },
  );
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
