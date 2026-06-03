/**
 * Roll back to / re-deploy a previous version by reusing its stored plan.
 * POST /api/deploy/redeploy   body: { workspaceId, deploymentId }
 *
 * Static route (same rationale as /api/deployment): the ids go in the body so
 * this wins over the catch-all rewrite.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  isApiConfigured,
  redeployVersion,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  let body: { workspaceId?: string; deploymentId?: string };
  try {
    body = (await request.json()) as {
      workspaceId?: string;
      deploymentId?: string;
    };
  } catch {
    return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
  }
  if (!body.workspaceId || !body.deploymentId) {
    return NextResponse.json(
      { detail: "workspaceId and deploymentId are required" },
      { status: 400 },
    );
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await redeployVersion(body.workspaceId, body.deploymentId, token);
    return NextResponse.json(data, { status: 202 });
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
