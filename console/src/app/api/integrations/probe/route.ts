/**
 * POST /api/integrations/probe — re-run the integration's health probe now.
 *
 * Pairs with the worker's cron loop: the cron picks up freshly-saved or stale
 * rows on its own cadence, but operators want a "is this still working?"
 * button right next to the row when they're debugging. This handler just
 * forwards to the backend's inline probe endpoint and bounces back to
 * `/integrations` so the page rerenders with the new status.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  probeIntegration,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const ALLOWED_KINDS = new Set([
  "linear",
  "jira",
  "github",
  "gitlab",
  "slack",
  "teams",
  "otel",
  "webhook",
  "s3-export",
]);

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const kind = (form.get("kind") ?? "").toString();

  if (!wsId || !ALLOWED_KINDS.has(kind)) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await probeIntegration(wsId, kind);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      // 409 = no secret stored yet; 404 = integration row gone. Both surface
      // as a query string so the page can show a tiny inline note next time.
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/integrations", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
