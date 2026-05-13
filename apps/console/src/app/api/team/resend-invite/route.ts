/**
 * Re-mint + re-email a pending workspace invite (B7 follow-up).
 *
 * Members page form handler. Calls ``POST /v1/workspaces/{ws}/invites/{id}/resend``
 * which rotates the underlying token (the old accept URL becomes
 * dead immediately) and queues a fresh transactional email through
 * SendGrid. The accept URL itself is returned by the backend so the
 * UI can offer it as a copy-link fallback when the email transport
 * is misconfigured; we stash it via the same per-id cookie the bulk
 * create flow uses, so the page reload renders it in the table.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  resendInvite,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { stashInviteTokens } from "@/lib/api/invite-stash";
import { workspaceMembersSettingsUrl } from "@/lib/members-settings-url";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const inviteId = (form.get("invite_id") ?? "").toString();

  if (!wsId || !inviteId)
    return NextResponse.redirect(workspaceMembersSettingsUrl(origin, undefined), 303);
  if (!isApiConfigured()) return back(origin, wsId, "api_unavailable");

  try {
    const refreshed = await resendInvite(wsId, inviteId);
    if (refreshed.accept_url) {
      await stashInviteTokens({ [refreshed.id]: refreshed.accept_url });
    }
    const url = workspaceMembersSettingsUrl(origin, wsId, {
      resent: refreshed.email_status ?? "1",
    });
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?next=%2Fsettings%3Ftab%3Dmembers", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "not_found");
      if (err.status === 409) return back(origin, wsId, "already_accepted");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }
}

function back(
  origin: string,
  workspaceId: string | undefined,
  reason: string,
) {
  const url = workspaceMembersSettingsUrl(origin, workspaceId, {
    invite_error: reason,
  });
  return NextResponse.redirect(url, 303);
}
