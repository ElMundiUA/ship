/**
 * Bulk create workspace invites (B7).
 *
 * Renders a ``POST`` from the members page: admin pastes a list of
 * emails, picks a role, and we mint one invite per email, then bounce
 * back to ``/members`` with ``?invited=N`` so the page can reload and
 * reveal the freshly-minted accept URLs.
 *
 * The plaintext tokens are echoed to the UI exactly once (the create
 * response); on the members page reload they're gone and the
 * accept-URL column is read from a cached session map keyed by
 * ``invite_id``.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createInvites,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { stashInviteTokens } from "@/lib/api/invite-stash";
import { workspaceMembersSettingsUrl } from "@/lib/members-settings-url";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const emails = (form.get("emails") ?? "").toString();
  const role = (form.get("role") ?? "member").toString();
  const ttl = Number(form.get("ttl_days") ?? "7") || 7;

  if (!wsId)
    return NextResponse.redirect(workspaceMembersSettingsUrl(origin, undefined), 303);
  if (!isApiConfigured()) return back(origin, wsId, "api_unavailable");
  if (!emails.trim()) return back(origin, wsId, "empty");

  try {
    const created = await createInvites(wsId, {
      emails,
      default_role: role,
      ttl_days: ttl,
    });
    // Stash tokens server-side keyed by invite id so the reload can
    // render the accept URLs without needing them in the URL itself.
    await stashInviteTokens(
      Object.fromEntries(
        created
          .filter((row) => row.token && row.accept_url)
          .map((row) => [row.id, row.accept_url as string]),
      ),
    );
    const url = workspaceMembersSettingsUrl(origin, wsId, {
      invited: String(created.length),
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
      if (err.status === 422) return back(origin, wsId, "bad_input");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }
}

function back(origin: string, workspaceId: string | undefined, reason: string) {
  const url = workspaceMembersSettingsUrl(origin, workspaceId, {
    invite_error: reason,
  });
  return NextResponse.redirect(url, 303);
}
