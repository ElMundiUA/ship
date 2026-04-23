/**
 * POST /api/inbox-groups/delete — delete an operational group.
 *
 * Bounces back to /settings/groups on success or with an error code
 * on failure (legacy form-action pattern, no JS required).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteInboxGroup,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const groupId = (form.get("group_id") ?? "").toString();

  if (!wsId || !groupId) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await deleteInboxGroup(wsId, groupId);
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings/groups?deleted=1", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/groups", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
