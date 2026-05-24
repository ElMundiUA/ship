/**
 * POST /api/bootstrap-plan  body: { ws, repo } — generate the bootstrap epic.
 *
 * Proxies the backend ``POST .../repos/{repo}/bootstrap/generate-plan`` so
 * the readiness card's "Generate bootstrap tickets" button can trigger it.
 * The backend 409s when the repo is already ready / has no blueprint / no
 * gaps / no tracker — those bubble through as the matching HTTP status so
 * the card can show the reason.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  generateBootstrapPlan,
  isApiConfigured,
} from "@/lib/api/client";


export async function POST(request: Request): Promise<Response> {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }
  let body: { ws?: string; repo?: string };
  try {
    body = (await request.json()) as { ws?: string; repo?: string };
  } catch {
    return NextResponse.json({ error: "invalid_json" }, { status: 400 });
  }
  const wsId = (body.ws || "").trim();
  const repoId = (body.repo || "").trim();
  if (!wsId || !repoId) {
    return NextResponse.json(
      { error: "ws_and_repo_required" },
      { status: 400 },
    );
  }
  try {
    const plan = await generateBootstrapPlan(wsId, repoId);
    return NextResponse.json(plan);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 502 });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.json(
          { error: "session_expired" },
          { status: 401 },
        );
      }
      // 409 carries the backend's reason (already ready / no blueprint /
      // no gaps / no tracker) in the detail — pass it through.
      return NextResponse.json(
        { error: `http_${err.status}`, detail: err.message },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
