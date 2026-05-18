/**
 * POST /api/env-separation-warnings/ack
 * Body: { ws, handle }
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  apiFetch,
  isApiConfigured,
} from "@/lib/api/client";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  let body: { ws?: string; handle?: string };
  try {
    body = (await request.json()) as { ws?: string; handle?: string };
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  const ws = (body.ws ?? "").trim();
  const handle = (body.handle ?? "").trim();
  if (!UUID_RE.test(ws) || handle.length < 8) {
    return NextResponse.json({ detail: "bad_input" }, { status: 400 });
  }
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  try {
    await apiFetch(`/v1/workspaces/${ws}/agent-runs/env-separation-warnings/ack`, {
      method: "POST",
      body: { handle },
    });
    return NextResponse.json({ ok: true });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
    }
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
