/**
 * POST /api/inbox-routing/delete — delete an inbox routing rule.
 *
 * The next intake for the now-orphaned handle falls back to the
 * built-in chain (workspace_admin → workspace_owner) — DELETE is a
 * soft-reset to defaults rather than a destructive op.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteInboxRoutingRule,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const ruleId = (form.get("rule_id") ?? "").toString();

  if (!wsId || !ruleId) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await deleteInboxRoutingRule(wsId, ruleId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  const url = new URL("/settings/inbox-routing", origin);
  url.searchParams.set("deleted", "1");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/inbox-routing", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
