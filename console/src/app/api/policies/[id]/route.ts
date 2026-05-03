/**
 * Browser-side proxy for editing a single workspace policy
 * (``PATCH`` / ``DELETE /v1/workspaces/{ws}/policies/{id}``).
 *
 * ``workspaceId`` is taken from the JSON body for PATCH and from
 * the query string for DELETE — DELETE requests shouldn't ship a
 * body. Both methods normalise the error envelope identically to
 * the Fleet-lanes proxy.
 */

import { NextResponse } from "next/server";

import {
  type ApiPolicyUpdateIn,
  ApiHttpError,
  ApiUnavailableError,
  deletePolicy,
  isApiConfigured,
  updatePolicy,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

type PatchBody = ApiPolicyUpdateIn & { workspaceId: string };

export async function PATCH(
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
  let body: PatchBody;
  try {
    body = (await request.json()) as PatchBody;
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
    const payload: ApiPolicyUpdateIn = {};
    if (body.title !== undefined) payload.title = body.title;
    if (body.body !== undefined) payload.body = body.body;
    if (body.enabled !== undefined) payload.enabled = body.enabled;
    if (body.sort_order !== undefined) payload.sort_order = body.sort_order;
    // ``applies_to_roles`` allows three states client-side: missing
    // (leave unchanged), null (clear to global), or an array. Use
    // ``"applies_to_roles" in body`` to distinguish missing from null
    // — a literal ``null`` value goes through to the backend.
    if ("applies_to_roles" in body) {
      payload.applies_to_roles = body.applies_to_roles ?? null;
    }
    const result = await updatePolicy(body.workspaceId, id, payload, token);
    return NextResponse.json(result, { status: 200 });
  } catch (err) {
    return errorResponse(err);
  }
}

export async function DELETE(
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
    await deletePolicy(workspaceId, id, token);
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
