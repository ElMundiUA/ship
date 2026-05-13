/**
 * Accept a workspace invite token (B7).
 *
 * Called from ``/invite?token=...`` after the invitee clicks
 * "Accept". The invitee must be signed in already; if not, we 302
 * to ``/login?next=/invite?token=...`` and come back here after
 * the round-trip.
 *
 * Success drops the user on the newly-joined workspace's dashboard.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  acceptInvite,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const token = (form.get("token") ?? "").toString();

  if (!token) return NextResponse.redirect(new URL("/", origin), 303);
  if (!isApiConfigured())
    return back(origin, token, "api_unavailable");

  try {
    const result = await acceptInvite(token);
    const dashboard = new URL("/", origin);
    dashboard.searchParams.set("ws", result.workspace_id);
    dashboard.searchParams.set("joined", "1");
    dashboard.searchParams.set("reason", "invite_accepted");
    return NextResponse.redirect(dashboard, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, token, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL(
            `/login?next=${encodeURIComponent(`/invite?token=${token}`)}`,
            origin,
          ),
          303,
        );
      if (err.status === 403) return back(origin, token, "wrong_email");
      if (err.status === 410) return back(origin, token, "expired");
      if (err.status === 404) return back(origin, token, "not_found");
      return back(origin, token, `http_${err.status}`);
    }
    return back(origin, token, "unknown");
  }
}

function back(origin: string, token: string, reason: string) {
  const url = new URL("/invite", origin);
  url.searchParams.set("token", token);
  url.searchParams.set("error", reason);
  return NextResponse.redirect(url, 303);
}
