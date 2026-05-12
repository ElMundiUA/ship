/**
 * POST /api/settings/workspace/create — create a new workspace under the
 * caller's personal org and bounce to it.
 *
 * The /settings/workspaces form posts ``name`` and ``slug``; we forward
 * both to ``POST /v1/workspaces``. On success the backend creates the
 * row, adds an owner ``WorkspaceMember`` for the caller, seeds default
 * knowledge + policies, and returns the new row. We then redirect to
 * ``/?ws=<id>`` so the global middleware writes the ``ship.ws`` cookie
 * (it does that whenever ``?ws=`` is hit) and the user lands on the
 * dashboard already pointed at the fresh tenancy.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createWorkspace,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const name = (form.get("name") ?? "").toString().trim();
  const slug = (form.get("slug") ?? "").toString().trim();
  if (!name || !slug) return back(origin, "bad_input");
  // Mirror the backend regex so the user gets a sensible error before
  // the round-trip rather than a generic 422 ``string_pattern_mismatch``.
  if (!/^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/.test(slug)) {
    return back(origin, "bad_slug");
  }
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  let createdId: string;
  try {
    const created = await createWorkspace({ name, slug });
    createdId = created.id;
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 409) return back(origin, "slug_taken");
      if (err.status === 422) return back(origin, "bad_input");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  const url = new URL("/", origin);
  url.searchParams.set("ws", createdId);
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/workspaces", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
