/**
 * Get a single deployment's status (lazily polls the provider server-side).
 * GET /api/deployment?ws=<workspaceId>&id=<deploymentId>
 *
 * NOTE: this is a STATIC route on purpose. A dynamic route like
 * `/api/deployments/[id]` is shadowed by the next.config `afterFiles`
 * rewrite (`/api/:path* → backend/v1/:path*`), which runs before dynamic
 * routes. Static routes win over that rewrite, so we pass the id as a query
 * param instead of a path segment.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  getDeployment,
  isApiConfigured,
} from "@/lib/api/client";
import { getSessionToken } from "@/lib/api/session";

export async function GET(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  const url = new URL(request.url);
  const ws = url.searchParams.get("ws");
  const id = url.searchParams.get("id");
  if (!ws || !id) {
    return NextResponse.json({ detail: "ws and id are required" }, { status: 400 });
  }
  try {
    const token = (await getSessionToken()) ?? undefined;
    const data = await getDeployment(ws, id, token);
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.detail ?? err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
