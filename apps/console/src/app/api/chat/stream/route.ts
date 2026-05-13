/**
 * SSE proxy for the C12 single-window chat.
 *
 * Next.js server components can't expose an SSE socket to the
 * browser directly, so the client opens ``POST /api/chat/stream``
 * and we forward the body + bearer token to the backend
 * ``/v1/workspaces/{ws}/chat/stream`` endpoint, piping the
 * ``text/event-stream`` response back unchanged.
 *
 * Phase 3b flipped the upstream wire to multipart/form-data so an
 * operator drag-drop carries images / PDFs / text alongside the
 * typed message body. We accept multipart from the browser and
 * relay it as-is — no buffering, no re-encoding — so a 30 MiB PDF
 * doesn't double-spool through Node's heap. JSON callers keep
 * working for back-compat: we build a synthetic FormData when the
 * incoming content-type is ``application/json``.
 */

import { getSessionToken } from "@/lib/api/session";
import { NextResponse } from "next/server";

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

  // Two wire shapes:
  //   - multipart/form-data (preferred — drag-drop + body + flags)
  //   - application/json (legacy — no attachments)
  // We normalize to FormData before forwarding so the backend
  // contract is one shape.
  const contentType = req.headers.get("content-type") || "";
  let form: FormData;
  let workspaceId: string | null = null;

  if (contentType.includes("multipart/form-data")) {
    try {
      form = await req.formData();
    } catch {
      return NextResponse.json({ error: "bad_multipart" }, { status: 400 });
    }
    workspaceId = (form.get("workspace_id") as string | null) ?? null;
    // Backend doesn't want workspace_id in the body — it lives in
    // the URL. Strip it before forwarding so the form payload stays
    // clean.
    form.delete("workspace_id");
  } else {
    // JSON path: build a FormData on the fly so the backend always
    // sees one wire shape. The legacy ``message`` alias maps to
    // ``body`` for compatibility with older clients.
    let json: {
      workspace_id?: string;
      message?: string;
      body?: string;
      classify_shift?: boolean;
      thread_id?: string;
    } = {};
    try {
      json = await req.json();
    } catch {
      return NextResponse.json({ error: "bad_json" }, { status: 400 });
    }
    workspaceId = json.workspace_id ?? null;
    const text =
      (typeof json.message === "string" && json.message.trim()) ||
      (typeof json.body === "string" && json.body.trim()) ||
      "";
    if (!text) {
      return NextResponse.json(
        { error: "message_required" },
        { status: 400 },
      );
    }
    form = new FormData();
    form.set("body", text);
    if (typeof json.classify_shift === "boolean") {
      form.set("classify_shift", String(json.classify_shift));
    }
    if (typeof json.thread_id === "string" && json.thread_id) {
      form.set("thread_id", json.thread_id);
    }
  }

  if (!workspaceId || typeof workspaceId !== "string") {
    return NextResponse.json(
      { error: "workspace_id_required" },
      { status: 400 },
    );
  }
  const bodyVal = form.get("body");
  if (typeof bodyVal !== "string" || !bodyVal.trim()) {
    return NextResponse.json(
      { error: "message_required" },
      { status: 400 },
    );
  }

  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(workspaceId)}/chat/stream`,
    {
      method: "POST",
      headers: {
        // Let fetch set the multipart boundary itself — overriding
        // content-type strips the boundary parameter and the
        // backend rejects the body.
        accept: "text/event-stream",
        authorization: `Bearer ${token}`,
      },
      body: form,
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
