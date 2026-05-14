/**
 * POST /api/local-tracker/rerun-ci — re-queue a completed memory
 * CI run. Pair to backend
 * ``POST /v1/workspaces/{ws}/local-tracker/repos/{owner}/{name}/runs/{run_id}/rerun``.
 */

import { NextResponse } from "next/server";

import { ApiHttpError, rerunLocalCiRun } from "@/lib/api/client";


type Body = {
  workspaceId?: unknown;
  owner?: unknown;
  name?: unknown;
  runId?: unknown;
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
  const owner = typeof body.owner === "string" ? body.owner : "";
  const name = typeof body.name === "string" ? body.name : "";
  const runId = typeof body.runId === "string" ? body.runId : "";
  if (!workspaceId || !owner || !name || !runId) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }
  try {
    await rerunLocalCiRun(workspaceId, owner, name, runId);
    return NextResponse.json({ ok: true });
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
