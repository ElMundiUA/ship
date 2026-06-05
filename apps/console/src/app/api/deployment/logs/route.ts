/**
 * Fetch one log stream (BUILD/DEPLOY/RUN) for a deployment.
 * GET /api/deployment/logs?ws=<workspaceId>&id=<deploymentId>&type=BUILD
 *
 * Static route on purpose (same reason as /api/deployment — see that file):
 * the id/type go as query params so this wins over the catch-all rewrite.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  getDeploymentLogs,
  isApiConfigured,
  type DeployLogType,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

const TYPES: DeployLogType[] = ["BUILD", "DEPLOY", "RUN"];

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  const url = new URL(request.url);
  const ws = url.searchParams.get("ws");
  const id = url.searchParams.get("id");
  const typeParam = (url.searchParams.get("type") ?? "BUILD").toUpperCase();
  const type = (TYPES.includes(typeParam as DeployLogType)
    ? typeParam
    : "BUILD") as DeployLogType;
  if (!ws || !id) {
    return NextResponse.json({ detail: "ws and id are required" }, { status: 400 });
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await getDeploymentLogs(ws, id, type, token);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
