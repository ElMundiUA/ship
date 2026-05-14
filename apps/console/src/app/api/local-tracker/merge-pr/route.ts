/**
 * POST /api/local-tracker/merge-pr — flip a memory PR to merged
 * and queue a deploy CI run. Pair to backend
 * ``POST /v1/workspaces/{ws}/local-tracker/repos/{owner}/{name}/pulls/{number}/merge``.
 */

import { NextResponse } from "next/server";

import { ApiHttpError, mergeLocalPullRequest } from "@/lib/api/client";


type Body = {
  workspaceId?: unknown;
  owner?: unknown;
  name?: unknown;
  number?: unknown;
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
  const numberRaw = body.number;
  const number =
    typeof numberRaw === "number"
      ? numberRaw
      : typeof numberRaw === "string"
        ? parseInt(numberRaw, 10)
        : NaN;
  if (!workspaceId || !owner || !name || !Number.isFinite(number)) {
    return NextResponse.json({ error: "bad_input" }, { status: 400 });
  }
  try {
    const pr = await mergeLocalPullRequest(workspaceId, owner, name, number);
    return NextResponse.json(pr);
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
