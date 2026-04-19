/**
 * POST /api/settings/catalog-sources — toggle which artifact-catalog layers
 * are merged for a workspace (global / workspace / project).
 *
 * The settings page renders one tiny form per toggle so we can keep the
 * page itself a server component (no client JS, no React state) and let
 * the browser do the round-trip. Each form posts `key` + `enabled` and we
 * forward to PATCH `/v1/workspaces/{id}` with a single-key partial update —
 * the backend merges it on top of the existing dict.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateWorkspace,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const VALID_KEYS = new Set(["global", "workspace", "project"]);

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const key = (form.get("key") ?? "").toString();
  const enabledRaw = (form.get("enabled") ?? "").toString();

  if (!wsId || !VALID_KEYS.has(key)) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) {
    return back(origin, "api_unavailable");
  }
  // Accept truthy strings ("on", "true", "1") as enabled — the form posts
  // the *target* state, not a boolean toggle, so we never get the legacy
  // "checkbox is missing means false" footgun.
  const enabled = enabledRaw === "true" || enabledRaw === "on" || enabledRaw === "1";

  try {
    await updateWorkspace(wsId, { catalog_sources: { [key]: enabled } });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
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
