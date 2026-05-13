/**
 * POST /api/integrations/upsert — save or rotate an integration secret.
 *
 * Same shape as the onboarding integration step but lives at a stable URL so
 * the `/integrations` management page can use it for any kind. Re-renders the
 * page via a 303 redirect on success/failure (no toast plumbing yet).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  upsertIntegration,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const ALLOWED_KINDS = new Set([
  "linear",
  "jira",
  "notion",
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

  // Empty secret means "edit config only" — null preserves existing value.
  const secretRaw = (form.get("secret") ?? "").toString();
  const secret = secretRaw.length > 0 ? secretRaw : null;

  const config: Record<string, string> = {};
  for (const [key, value] of form.entries()) {
    if (!key.startsWith("config_")) continue;
    const k = key.slice("config_".length);
    const v = value.toString().trim();
    if (k && v) config[k] = v;
  }

  try {
    await upsertIntegration(wsId, kind, { config, secret });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  // Redirect back to /integrations with no query so the page rerenders with
  // fresh server-side data.
  return NextResponse.redirect(new URL("/integrations", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/integrations", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
