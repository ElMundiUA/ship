/**
 * Browser-side proxy for deleting a workspace Fleet lane
 * (``DELETE /v1/workspaces/{ws}/fleet-lanes/{id}``).
 *
 * Takes ``workspaceId`` as a query string because DELETE requests
 * shouldn't ship a body — everything else matches the standard
 * proxy envelope.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteFleetLane,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

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
    await deleteFleetLane(workspaceId, id, token);
    return new NextResponse(null, { status: 204 });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error: "Backend is unreachable.", code: "api_unavailable" },
        { status: 503 },
      );
    }
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { error: String(err.detail ?? err.message), status: err.status },
        { status: err.status },
      );
    }
    return NextResponse.json(
      { error: err instanceof Error ? err.message : "Unknown error" },
      { status: 500 },
    );
  }
}
