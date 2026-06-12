/**
 * List deploy providers + connected status for a workspace.
 * GET /api/deploy/providers?ws=<workspaceId>
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  listDeployProviders,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  const ws = new URL(request.url).searchParams.get("ws");
  if (!ws) {
    return NextResponse.json({ detail: "ws is required" }, { status: 400 });
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await listDeployProviders(ws, token);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
