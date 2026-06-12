/**
 * Start the DigitalOcean OAuth connect; returns the URL to redirect to.
 * POST /api/deploy/connect   body: { workspaceId, returnPath? }
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  startDigitalOceanConnect,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: { workspaceId?: string; returnPath?: string };
  try {
    body = (await request.json()) as { workspaceId?: string; returnPath?: string };
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.workspaceId) {
    return NextResponse.json({ detail: "workspaceId is required" }, { status: 400 });
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await startDigitalOceanConnect(
      body.workspaceId,
      body.returnPath,
      token,
    );
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
