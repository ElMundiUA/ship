/**
 * GET /api/env-separation-warnings?ws=<uuid>
 * Proxies pending env-separation modals for the workspace.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  apiFetch,
  isApiConfigured,
} from "@/lib/api/client";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type EnvSeparationWarning = {
  handle: string;
  project_id: string;
  project_name: string;
};

export async function GET(request: Request) {
  const ws = new URL(request.url).searchParams.get("ws") ?? "";
  if (!UUID_RE.test(ws)) {
    return NextResponse.json({ detail: "bad_workspace" }, { status: 400 });
  }
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }
  try {
    const rows = await apiFetch<EnvSeparationWarning[]>(
      `/v1/workspaces/${ws}/agent-runs/env-separation-warnings`,
    );
    return NextResponse.json(rows);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
    }
    if (err instanceof ApiHttpError) {
      return NextResponse.json({ detail: err.message }, { status: err.status });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}
