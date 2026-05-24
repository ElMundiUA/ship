/**
 * GET /api/sdlc-readiness?ws=<id>&repo=<id> — bootstrap readiness for a repo.
 *
 * Proxies the backend ``GET /v1/workspaces/{ws}/repos/{repo}/sdlc-readiness``
 * so the client-side readiness card (which can't read the session cookie
 * directly) can fetch on demand. Lazy by design — only called when the
 * operator expands a repo's readiness, since each assessment is a couple
 * of GitHub API calls server-side.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  getSdlcReadiness,
  isApiConfigured,
} from "@/lib/api/client";


export async function GET(request: Request): Promise<Response> {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }
  const url = new URL(request.url);
  const wsId = (url.searchParams.get("ws") || "").trim();
  const repoId = (url.searchParams.get("repo") || "").trim();
  if (!wsId || !repoId) {
    return NextResponse.json(
      { error: "ws_and_repo_required" },
      { status: 400 },
    );
  }
  try {
    const readiness = await getSdlcReadiness(wsId, repoId);
    return NextResponse.json(readiness);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 502 });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.json(
          { error: "session_expired" },
          { status: 401 },
        );
      }
      return NextResponse.json(
        { error: `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
