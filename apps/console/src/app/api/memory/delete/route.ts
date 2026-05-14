/**
 * POST /api/memory/delete — hard-delete one Navigator memory fact.
 *
 * Pair to ``/v1/workspaces/{ws}/navigator-memories/{id} DELETE``.
 * Exists because the Memory page client island ("use client") can't
 * import the API client directly — ``client.ts`` declares
 * ``import "server-only"`` so the session token never leaks into
 * the browser bundle. This handler is the server-side shim.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteNavigatorMemory,
} from "@/lib/api/client";


type Body = { workspaceId?: unknown; memoryId?: unknown };


export async function POST(request: Request) {
  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }

  const workspaceId = typeof body.workspaceId === "string" ? body.workspaceId : "";
  const memoryId = typeof body.memoryId === "string" ? body.memoryId : "";
  if (!workspaceId || !memoryId) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }

  try {
    await deleteNavigatorMemory(workspaceId, memoryId);
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

  return NextResponse.json({ ok: true });
}
