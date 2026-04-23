/**
 * POST /api/inbox-groups/create — create an operational group.
 *
 * Server-action endpoint for the /settings/groups page. Forwards the
 * form payload to `POST /v1/workspaces/{ws}/inbox/groups` and bounces
 * back to the page with a query-string error code on failure so the
 * server component can render a human-readable banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createInboxGroup,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const VALID_STRATEGIES = ["round_robin", "oncall", "first"] as const;
type Strategy = (typeof VALID_STRATEGIES)[number];

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const key = (form.get("key") ?? "").toString().trim().toLowerCase();
  const name = (form.get("name") ?? "").toString().trim();
  const description = (form.get("description") ?? "").toString().trim();
  const strategyRaw = (form.get("strategy") ?? "round_robin").toString();
  const strategy = (
    VALID_STRATEGIES.includes(strategyRaw as Strategy)
      ? strategyRaw
      : "round_robin"
  ) as Strategy;

  if (!wsId || !key || !name) return back(origin, "bad_input");
  if (!/^[a-z][a-z0-9_]*$/.test(key)) return back(origin, "bad_key");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await createInboxGroup(wsId, {
      key,
      name,
      description: description || null,
      assignment_strategy: strategy,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 409) return back(origin, "duplicate_key");
      if (err.status === 422) return back(origin, "invalid_input");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings/groups", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/groups", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
