/**
 * Bucket listing + creation proxy for the C12 sidebar.
 *
 * We keep the session token in an httpOnly cookie, so the
 * BucketsSidebar client component can't call the backend directly
 * — it goes through this handler instead. The route supports both
 * ``GET`` (list) and ``POST`` (create); per-bucket PATCH lives in
 * ``[slug]/route.ts``.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

function resolveBase(): string | null {
  const base = process.env.SHIP_API_URL?.trim().replace(/\/+$/, "") ?? "";
  return base.length > 0 ? base : null;
}

export async function GET(req: Request): Promise<Response> {
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
  const includeArchived =
    url.searchParams.get("include_archived") === "1" ||
    url.searchParams.get("include_archived") === "true";

  const qs = includeArchived ? "?include_archived=true" : "";
  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets${qs}`,
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

export async function POST(req: Request): Promise<Response> {
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
  const body = await req.text();
  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/buckets`,
    {
      method: "POST",
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
