/**
 * POST /api/settings/default-agent — set the workspace-level default
 * agent profile (Claude main / cheaper / cursor / codex / etc).
 *
 * Server-action endpoint for the General settings card. Forwards to
 * PATCH /v1/workspaces/{id} with a single-field partial update; the
 * /process editor then resolves per-state ``"auto"`` profiles to this
 * value when projecting the canvas. Without it set, the editor blocks
 * all edits behind a gating banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateWorkspace,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

// Mirror of backend ``_PROCESS_AGENT_PROFILES`` (see repos.py). Kept
// in sync by hand — the picker UI and this guard read the same set.
const VALID_PROFILES = new Set([
  "auto",
  "main",
  "cheaper",
  "cursor_agent",
  "codex_cli",
  "ship_cloud_agent",
  "local_cli",
]);

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const profile = (form.get("profile") ?? "").toString();

  if (!wsId || !VALID_PROFILES.has(profile)) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) {
    return back(origin, "api_unavailable");
  }

  try {
    await updateWorkspace(wsId, { default_agent_profile: profile });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings/general", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/general", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
