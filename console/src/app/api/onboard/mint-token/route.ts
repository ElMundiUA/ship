/**
 * Onboarding step 3 — mint a workspace-scoped Personal Access Token for shipctl.
 *
 * This one returns JSON (not a redirect) because the plaintext secret is shown
 * exactly once and can never be re-fetched. The wizard's client component reads
 * the response and renders the secret with a copy button.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  mintToken,
} from "@/lib/api/client";

export async function POST(request: Request) {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }

  let payload: { workspace_id?: string; name?: string };
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }

  const workspaceId = payload.workspace_id?.toString();
  if (!workspaceId) {
    return NextResponse.json({ error: "missing_workspace" }, { status: 400 });
  }

  const name = (payload.name?.toString() || "shipctl on this laptop").slice(0, 120);

  try {
    const minted = await mintToken({
      name,
      workspace_id: workspaceId,
      scopes: ["workspace:read", "workspace:write"],
      ttl_days: 90,
    });
    return NextResponse.json(minted, { status: 201 });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) return NextResponse.json({ error: "session_expired" }, { status: 401 });
      return NextResponse.json(
        { error: typeof err.detail === "string" ? err.detail : `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
