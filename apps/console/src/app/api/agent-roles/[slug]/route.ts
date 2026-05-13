/**
 * Browser-side proxy for editing or deleting a workspace agent role
 * (``PUT`` / ``DELETE /v1/workspaces/{ws}/agent-roles/{slug}``).
 *
 * ``workspaceId`` is read from the JSON body for PUT and from the
 * query string for DELETE — DELETE requests shouldn't ship a body.
 */

import { NextResponse } from "next/server";

import {
  type ApiAgentRoleUpdateIn,
  ApiHttpError,
  ApiUnavailableError,
  deleteWorkspaceAgentRole,
  getShipAgentRoleDefault,
  getWorkspaceAgentRole,
  isApiConfigured,
  updateWorkspaceAgentRole,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type PutBody = ApiAgentRoleUpdateIn & { workspaceId: string };

/**
 * Fetch a body for the editor side panel.
 *
 * - ``?workspaceId=X`` → the workspace row body (override or clone).
 * - ``?defaultOnly=1`` → the Ship default body (no workspace lookup).
 *
 * Returning the body server-side keeps the session token off the
 * client and lets the same endpoint serve both flavours.
 */
export async function GET(
  request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const { slug } = await params;
  const url = new URL(request.url);
  const workspaceId = url.searchParams.get("workspaceId");
  const defaultOnly = url.searchParams.get("defaultOnly") === "1";

  try {
    const token = (await getSessionToken()) ?? undefined;
    if (defaultOnly) {
      const detail = await getShipAgentRoleDefault(slug, token);
      return NextResponse.json(detail, { status: 200 });
    }
    if (!workspaceId) {
      return NextResponse.json(
        { error: "workspaceId query param is required.", code: "bad_request" },
        { status: 400 },
      );
    }
    const detail = await getWorkspaceAgentRole(workspaceId, slug, token);
    return NextResponse.json(detail, { status: 200 });
  } catch (err) {
    return errorResponse(err);
  }
}

export async function PUT(
  request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const { slug } = await params;
  let body: PutBody;
  try {
    body = (await request.json()) as PutBody;
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
    const payload: ApiAgentRoleUpdateIn = {};
    if (body.name !== undefined) payload.name = body.name;
    if (body.prompt !== undefined) payload.prompt = body.prompt;
    const result = await updateWorkspaceAgentRole(
      body.workspaceId,
      slug,
      payload,
      token,
    );
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    return errorResponse(err);
  }
}

export async function DELETE(
  request: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "Backend is not configured.", code: "api_unavailable" },
      { status: 503 },
    );
  }

  const { slug } = await params;
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
    await deleteWorkspaceAgentRole(workspaceId, slug, token);
    return new NextResponse(null, { status: 204 });
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
