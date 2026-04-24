/**
 * Onboarding step — kick off Linear or Notion OAuth, or skip the tracker
 * setup entirely.
 *
 * Form submits with `kind=linear|notion|github|skip`:
 *
 * - `linear` / `notion` — POST to backend `install/start` for the chosen
 *   vendor, then 303-redirect to the returned `install_url`. The vendor
 *   sends the user to its own OAuth approval page; the backend's
 *   `install/callback` handles persistence and bounces them back into
 *   `?step=done` on the console origin.
 * - `github` — no extra OAuth needed; the GitHub App installed on the
 *   first wizard step already grants Issues access. We just bounce
 *   forward to the done step.
 * - `skip` — same forward bounce, no API calls.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  startLinearInstall,
  startNotionInstall,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const kind = (form.get("kind") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (kind === "skip" || kind === "github") {
    return forward(origin, wsId);
  }

  if (kind !== "linear" && kind !== "notion") {
    return wizardError(origin, wsId, "bad_kind");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  try {
    const start =
      kind === "linear"
        ? await startLinearInstall(wsId)
        : await startNotionInstall(wsId);
    return NextResponse.redirect(start.install_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return wizardError(origin, wsId, "forbidden");
      // 503 = backend says the OAuth app is not configured (missing
      // {LINEAR,NOTION}_CLIENT_ID / SECRET). We embed the kind so the
      // wizard banner can call out which vendor needs ops attention
      // (and that GitHub Issues remains a viable alternative).
      if (err.status === 503)
        return wizardError(origin, wsId, `not_configured_${kind}`);
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }
}

function forward(origin: string, wsId: string) {
  // Wave-8c: after the workspace-level tracker OAuth, drop the user
  // into the per-repo Confirm bootstrap step. That's where they
  // review the canonical Plays bundle, bind a tracker / push agent
  // secrets per repo and open the unified seed PR.
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "confirm");
  url.searchParams.set("ws", wsId);
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "tracker");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
