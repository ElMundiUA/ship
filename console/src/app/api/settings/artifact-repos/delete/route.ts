/**
 * POST /api/settings/artifact-repos/delete — drop a registered artifact repo.
 *
 * The settings page renders a tiny one-button form per row so the click
 * round-trips through this handler instead of the cookie-eating Server
 * Action codepath. We forward to DELETE
 * `/v1/workspaces/{id}/artifact-repos/{repoId}`.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteArtifactRepo,
  isApiConfigured,
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
    await deleteArtifactRepo(wsId, repoId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
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
