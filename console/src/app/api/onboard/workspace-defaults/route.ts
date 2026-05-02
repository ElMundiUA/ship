/**
 * PATCH /api/onboard/workspace-defaults — JSON variant of the
 * workspace-update path used by the onboarding confirm step's
 * defaults panel.
 *
 * The legacy ``/api/settings/default-agent`` route is form-encoded
 * + 303-redirect — fine for the standalone settings page but
 * awkward inside the wizard panel where we want inline ``await
 * fetch(...)`` UX. This endpoint is JSON-in / JSON-out and forwards
 * the same single-field PATCH to the backend.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateWorkspace,
} from "@/lib/api/client";

const VALID_PROFILES = new Set([
  "auto",
  "main",
  "cheaper",
  "cursor_agent",
  "codex_cli",
  "ship_cloud_agent",
  "local_cli",
]);

export async function PATCH(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error: "api_unavailable" },
      { status: 503 },
    );
  }

  let body: { workspace_id?: unknown; default_agent_profile?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }

  const workspaceId =
    typeof body.workspace_id === "string" ? body.workspace_id.trim() : "";
  const profile =
    typeof body.default_agent_profile === "string"
      ? body.default_agent_profile.trim()
      : "";

  if (!workspaceId || !VALID_PROFILES.has(profile)) {
    return NextResponse.json({ error: "bad_input" }, { status: 422 });
  }

  try {
    const out = await updateWorkspace(workspaceId, {
      default_agent_profile: profile,
    });
    return NextResponse.json({ workspace: out });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error: "api_unavailable" },
        { status: 503 },
      );
    }
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { error: `http_${err.status}`, detail: err.detail ?? null },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
