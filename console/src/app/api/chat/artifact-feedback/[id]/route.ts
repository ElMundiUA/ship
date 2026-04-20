/**
 * Artifact feedback triage proxy.
 *
 * The inbox page renders the feedback list server-side; edits from
 * the ``FeedbackRow`` client component go through here so the
 * ``ship_session`` cookie's bearer token stays on the server.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await params;
  const base =
    process.env.SHIP_API_URL?.trim().replace(/\/+$/, "") ?? "";
  if (!base) {
    return NextResponse.json(
      { error: "backend_not_configured" },
      { status: 503 },
    );
  }
  const token = await getSessionToken();
  if (!token) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }
  const url = new URL(req.url);
  const workspaceId = url.searchParams.get("workspace_id");
  if (!workspaceId) {
    return NextResponse.json(
      { error: "workspace_id_required" },
      { status: 400 },
    );
  }
  const body = await req.text();
  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/artifact-feedback/${encodeURIComponent(id)}`,
    {
      method: "PATCH",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        authorization: `Bearer ${token}`,
      },
      body,
      cache: "no-store",
    },
  );
  const text = await upstream.text();
  return new Response(text, {
    status: upstream.status,
    headers: {
      "content-type":
        upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
