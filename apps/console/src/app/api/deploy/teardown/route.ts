/**
 * Tear down (delete) an app — really removes it from DigitalOcean (stops
 * billing) and drops its deployment rows.
 * POST /api/deploy/teardown   body: { workspaceId, repoId }
 *
 * Static route on purpose (dynamic routes are shadowed by the next.config
 * afterFiles rewrite). POST, not DELETE, to keep the same simple shape as
 * the other deploy handlers.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  teardownDeployment,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: { workspaceId?: string; repoId?: string };
  try {
    body = (await request.json()) as { workspaceId?: string; repoId?: string };
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.workspaceId || !body.repoId) {
    return NextResponse.json(
      { detail: "workspaceId and repoId are required" },
      { status: 400 },
    );
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await teardownDeployment(body.workspaceId, body.repoId, token);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
