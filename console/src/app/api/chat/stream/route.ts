/**
 * SSE proxy for the C12 single-window chat.
 *
 * Next.js server components can't expose an SSE socket to the
 * browser directly, so the client opens ``POST /api/chat/stream``
 * and we forward the body + bearer token to the backend
 * ``/v1/workspaces/{ws}/chat/stream`` endpoint, piping the
 * ``text/event-stream`` response back unchanged.
 *
 * Keeping the session token in an httpOnly cookie means the browser
 * never touches it. The only thing the caller has to supply is the
 * workspace id and the message body, which we validate minimally
 * before forwarding.
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
    message?: string;
    force_new_thread?: boolean;
  } = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  const { workspace_id, message, force_new_thread } = body;
  if (!workspace_id || typeof workspace_id !== "string") {
    return NextResponse.json({ error: "workspace_id_required" }, { status: 400 });
  }
  if (!message || typeof message !== "string" || message.trim().length === 0) {
    return NextResponse.json({ error: "message_required" }, { status: 400 });
  }

  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspace_id)}/chat/stream`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "text/event-stream",
        authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ message, force_new_thread: !!force_new_thread }),
      // Disable any fetch cache — this is a live stream.
      cache: "no-store",
    },
  );

  if (!upstream.ok || !upstream.body) {
    const detail = await upstream.text().catch(() => "");
    return NextResponse.json(
      { error: "upstream_error", status: upstream.status, detail },
      { status: upstream.status || 502 },
    );
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-cache, no-transform",
      // Disable buffering on NGINX-style reverse proxies so the
      // browser sees deltas as they're produced upstream.
      "x-accel-buffering": "no",
      connection: "keep-alive",
    },
  });
}
