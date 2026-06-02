/**
 * Activity feed for one app.
 * GET /api/deploy/events?ws=<workspaceId>&repoId=<repoId>
 *
 * Static route (dynamic routes are shadowed by the next.config rewrite).
 */

import { NextResponse } from "next/server";

import { ApiHttpError, isApiConfigured, listAppEvents } from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  const url = new URL(request.url);
  const ws = url.searchParams.get("ws");
  const repoId = url.searchParams.get("repoId");
  if (!ws || !repoId) {
    return NextResponse.json({ detail: "ws and repoId are required" }, { status: 400 });
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await listAppEvents(ws, repoId, token);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
