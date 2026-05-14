/**
 * POST /api/memory/forget — bulk-delete Navigator memory facts
 * captured in the last N days.
 *
 * Pair to ``POST /v1/workspaces/{ws}/navigator-memories/forget``.
 * Same rationale as ``/api/memory/delete`` — keeps the session
 * token out of the browser bundle.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  bulkForgetNavigatorMemories,
} from "@/lib/api/client";


type Body = { workspaceId?: unknown; days?: unknown };


export async function POST(request: Request) {
  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }

  const workspaceId = typeof body.workspaceId === "string" ? body.workspaceId : "";
  const daysNum =
    typeof body.days === "number"
      ? body.days
      : typeof body.days === "string"
        ? parseInt(body.days, 10)
        : NaN;
  if (!workspaceId || !Number.isFinite(daysNum) || daysNum < 1 || daysNum > 90) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }

  try {
    const result = await bulkForgetNavigatorMemories(workspaceId, daysNum);
    return NextResponse.json(result);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
    }
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { error: `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
