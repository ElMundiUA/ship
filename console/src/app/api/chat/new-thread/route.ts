/**
 * Archive the active chat thread and start a fresh one.
 *
 * Matches the C12 single-window UX: when the user hits "New
 * conversation" (or accepts the topic-shift banner), we POST here
 * and proxy through to the backend ``/chat/active/new`` endpoint.
 * The session token stays in the httpOnly cookie — the browser
 * never sees it.
 */

import { NextResponse } from "next/server";

import { getSessionToken } from "@/lib/api/session";

export const runtime = "nodejs";

export async function POST(req: Request): Promise<Response> {
  const base = process.env.SHIP_API_URL?.trim().replace(/\/+$/, "") ?? "";
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

  let body: {
    workspace_id?: string;
    title?: string | null;
    pack_into_bucket_slug?: string | null;
    pack_into_bucket_name?: string | null;
  } = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  const { workspace_id, title, pack_into_bucket_slug, pack_into_bucket_name } =
    body;
  if (!workspace_id || typeof workspace_id !== "string") {
    return NextResponse.json(
      { error: "workspace_id_required" },
      { status: 400 },
    );
  }

  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspace_id)}/chat/active/new`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        title: title ?? null,
        pack_into_bucket_slug: pack_into_bucket_slug ?? null,
        pack_into_bucket_name: pack_into_bucket_name ?? null,
      }),
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
