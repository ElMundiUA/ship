/**
 * POST /api/inbox-routing/preview — dry-run resolve a handle.
 *
 * Server-action wrapper around `POST /v1/workspaces/{ws}/inbox/routing/preview`.
 * The backend wraps the resolver call in a SAVEPOINT and rolls it back
 * unconditionally, so this endpoint is side-effect free by design (no
 * round_robin pointer is nudged when an admin pokes "preview").
 *
 * The result is bounced back to /settings/inbox-routing as query params
 * (`preview=<handle>&preview_user=<email|''>&preview_reason=<reason>&preview_intake=<intake_handle>`)
 * so the page can render a flash banner without holding any server-side
 * session state. URL-encoded so handles like "repo_maintainer" survive.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  previewInboxRouting,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const handle = (form.get("handle") ?? "").toString().trim().toLowerCase();
  const ruleId = (form.get("rule_id") ?? "").toString();

  if (!wsId || !handle) return back(origin, "bad_input", { handle, ruleId });
  if (!/^[a-z][a-z0-9_]*$/.test(handle)) {
    return back(origin, "validation_failed", { handle, ruleId });
  }
  if (!isApiConfigured()) return back(origin, "api_unavailable", { handle, ruleId });

  try {
    const result = await previewInboxRouting(wsId, { handle });
    const url = new URL("/settings/inbox-routing", origin);
    if (ruleId) url.searchParams.set("rule", ruleId);
    url.searchParams.set("preview", handle);
    url.searchParams.set(
      "preview_user",
      result.resolved_user_email ?? "",
    );
    url.searchParams.set("preview_reason", result.intake_reason);
    url.searchParams.set("preview_intake", result.intake_handle);
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, "api_unavailable", { handle, ruleId });
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 403) return back(origin, "forbidden", { handle, ruleId });
      if (err.status === 404) return back(origin, "not_found", { handle, ruleId });
      if (err.status === 422) {
        // The resolver itself failed (e.g. a rule pointing at a
        // missing group). Surface the raw detail in the preview slot
        // so the admin sees exactly what would happen at intake.
        const url = new URL("/settings/inbox-routing", origin);
        if (ruleId) url.searchParams.set("rule", ruleId);
        url.searchParams.set("preview", handle);
        url.searchParams.set("preview_user", "");
        url.searchParams.set(
          "preview_reason",
          typeof err.detail === "string" ? err.detail : "resolver_error",
        );
        url.searchParams.set("preview_intake", handle);
        url.searchParams.set("preview_error", "1");
        return NextResponse.redirect(url, 303);
      }
      return back(origin, `http_${err.status}`, { handle, ruleId });
    }
    return back(origin, "unknown", { handle, ruleId });
  }
}

function back(
  origin: string,
  code: string,
  ctx: { handle?: string; ruleId?: string } = {},
) {
  const url = new URL("/settings/inbox-routing", origin);
  if (ctx.ruleId) url.searchParams.set("rule", ctx.ruleId);
  if (ctx.handle) url.searchParams.set("preview", ctx.handle);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
