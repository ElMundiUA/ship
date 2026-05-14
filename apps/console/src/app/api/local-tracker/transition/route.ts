/**
 * POST /api/local-tracker/transition — bump a memory ticket's
 * stage label. Pair to backend
 * ``POST /v1/workspaces/{ws}/local-tracker/tickets/{display_id}/transition``.
 */

import { NextResponse } from "next/server";

import { ApiHttpError, transitionLocalTicket } from "@/lib/api/client";


type Body = {
  workspaceId?: unknown;
  displayId?: unknown;
  toState?: unknown;
};


export async function POST(request: Request) {
  let body: Body;
  try {
    body = (await request.json()) as Body;
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  const workspaceId =
    typeof body.workspaceId === "string" ? body.workspaceId : "";
  const displayId =
    typeof body.displayId === "string" ? body.displayId : "";
  const toState = typeof body.toState === "string" ? body.toState : "";
  if (!workspaceId || !displayId || !toState) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }
  try {
    const ticket = await transitionLocalTicket(workspaceId, displayId, toState);
    return NextResponse.json(ticket);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { error: `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
