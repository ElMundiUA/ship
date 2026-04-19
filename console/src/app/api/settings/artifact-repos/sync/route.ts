/**
 * POST /api/settings/artifact-repos/sync — force an immediate sync.
 *
 * Mirrors the delete handler: each row gets its own micro-form so the
 * "Sync now" button works without client JS. Forwards to
 * POST `/v1/workspaces/{id}/artifact-repos/{repoId}/sync`, which clones or
 * fetches synchronously and returns the updated row. We don't render the
 * response here — we just bounce back to /settings, which re-fetches and
 * shows the new ``last_sync_*`` columns.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  syncArtifactRepo,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo") ?? "").toString();
  if (!wsId || !repoId) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await syncArtifactRepo(wsId, repoId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      // 502 = git failed; the row's last_sync_error already carries the
      // human-readable detail, so just bounce back and let the table show it.
      if (err.status === 502) return NextResponse.redirect(new URL("/settings", origin), 303);
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
