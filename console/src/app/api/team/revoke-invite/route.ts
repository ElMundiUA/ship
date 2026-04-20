/**
 * Revoke a pending workspace invite (B7).
 *
 * Members page form handler. No confirm step — the accept URL
 * becomes dead immediately, but admin can just re-issue a fresh
 * invite for the same email and the backend's "reuse the pending
 * row" logic keeps everything tidy.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  revokeInvite,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const inviteId = (form.get("invite_id") ?? "").toString();

  if (!wsId || !inviteId)
    return NextResponse.redirect(new URL("/members", origin), 303);
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    await revokeInvite(wsId, inviteId);
    const url = new URL("/members", origin);
    url.searchParams.set("revoked", "1");
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?next=%2Fmembers", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 404) return back(origin, "not_found");
      if (err.status === 409) return back(origin, "already_accepted");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }
}

function back(origin: string, reason: string) {
  const url = new URL("/members", origin);
  url.searchParams.set("invite_error", reason);
  return NextResponse.redirect(url, 303);
}
