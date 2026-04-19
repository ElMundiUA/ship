/**
 * POST /api/settings/workspace/delete — destroy a workspace.
 *
 * The Danger zone form posts the workspace id and a slug confirmation; we
 * forward both to `DELETE /v1/workspaces/{id}`. On success we redirect to
 * /workspaces (or `/onboarding` once it lands) so the operator sees the
 * remaining tenancy or has somewhere to go from scratch.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteWorkspace,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const slug = (form.get("slug_confirmation") ?? "").toString().trim();
  if (!wsId || !slug) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await deleteWorkspace(wsId, slug);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      if (err.status === 409) return back(origin, "slug_mismatch");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  // Bounce somewhere safe. If they had only one workspace, the dashboard
  // will show its empty state; otherwise the next workspace becomes
  // implicit-current.
  return NextResponse.redirect(new URL("/", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
