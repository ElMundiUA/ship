/**
 * Per-bucket GET / PATCH proxy for the C12 sidebar.
 *
 * ``GET`` returns bucket metadata plus summary count; ``PATCH``
 * handles rename / description edits / archive toggling from the
 * sidebar. All calls forward the ``ship_session`` cookie's bearer
 * token upstream.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

function resolveBase(): string | null {
  const base = process.env.SHIP_API_URL?.trim().replace(/\/+$/, "") ?? "";
  return base.length > 0 ? base : null;
}

async function proxy(
  req: Request,
  slug: string,
  method: "GET" | "PATCH",
): Promise<Response> {
  const base = resolveBase();
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
  const body = method === "PATCH" ? await req.text() : undefined;
  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets/${encodeURIComponent(slug)}`,
    {
      method,
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

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<Response> {
  const { slug } = await params;
  return proxy(req, slug, "GET");
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
): Promise<Response> {
  const { slug } = await params;
  return proxy(req, slug, "PATCH");
}
