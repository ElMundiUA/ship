/**
 * Form handler — re-pull `.ship/config.yml` for one repo.
 *
 * Wires the `/lanes` page's "Sync now" button to the backend's
 * `POST /v1/workspaces/{ws}/repos/{repo_id}/lanes/sync`. Admin-only
 * on the backend side — non-admins get bounced back with a
 * `?reason=forbidden` so the UI can render a banner.
 *
 * Form-driven (no fetch from the browser) to keep the session token
 * in the httpOnly cookie and the whole page server-renderable.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  syncRepoLanes,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo") ?? "").toString();

  if (!wsId || !repoId) {
    return NextResponse.redirect(new URL("/lanes", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, repoId, "api_unavailable");
  }

  try {
    const result = await syncRepoLanes(wsId, repoId);
    const reason = result.errors.length > 0 ? "synced_with_errors" : "synced";
    const changed = result.added + result.updated + result.removed;
    return back(origin, wsId, repoId, reason, String(changed));
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, repoId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, repoId, "forbidden");
      if (err.status === 404) return back(origin, wsId, repoId, "missing");
      if (err.status === 409) return back(origin, wsId, repoId, "missing_config");
      if (err.status === 502) return back(origin, wsId, repoId, "github_unreachable");
      return back(origin, wsId, repoId, `http_${err.status}`);
    }
    return back(origin, wsId, repoId, "unknown");
  }
}

function back(
  origin: string,
  wsId: string,
  repoId: string,
  reason: string,
  changed?: string,
) {
  const url = new URL("/lanes", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("synced", repoId);
  url.searchParams.set("reason", reason);
  if (changed !== undefined) url.searchParams.set("changed", changed);
  return NextResponse.redirect(url, 303);
}
