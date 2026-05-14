/**
 * E20-3 — flip the active thread's intent in place.
 *
 * Pairs with backend's POST /v1/workspaces/{ws}/chat/active/intent
 * (E20-1). Proxies the call server-side so the session token stays
 * in the httpOnly cookie. Used by the drafting-intent inline CTA
 * in the single-window chat — clicking "switch to drafting" /
 * "exit drafting" lands here, the backend flips intent + audit-
 * logs, and we return the updated thread shape the Console folds
 * back into its state.
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
    intent?: "shape_project" | null;
  } = {};
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  const { workspace_id, intent } = body;
  if (!workspace_id || typeof workspace_id !== "string") {
    return NextResponse.json(
      { error: "missing_workspace_id" },
      { status: 400 },
    );
  }
  if (intent !== null && intent !== "shape_project") {
    return NextResponse.json(
      { error: "invalid_intent" },
      { status: 400 },
    );
  }

  const upstream = await fetch(
    `${base}/v1/workspaces/${encodeURIComponent(
      workspace_id,
    )}/chat/active/intent`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ intent }),
    },
  );

  const text = await upstream.text();
  return new NextResponse(text, {
    status: upstream.status,
    headers: {
      "content-type":
        upstream.headers.get("content-type") ?? "application/json",
    },
  });
}
