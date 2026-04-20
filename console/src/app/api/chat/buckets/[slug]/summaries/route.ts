/**
 * Bucket summary listing proxy for the C12 sidebar.
 *
 * Returns the packed-thread rollups belonging to a single bucket.
 * The client component fetches this every time the user selects
 * a bucket in the sidebar.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<Response> {
  const { slug } = await params;
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
  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}/summaries`,
    {
      headers: {
        accept: "application/json",
        authorization: `Bearer ${token}`,
      },
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
