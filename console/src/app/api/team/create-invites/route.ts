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

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const emails = (form.get("emails") ?? "").toString();
  const role = (form.get("role") ?? "member").toString();
  const ttl = Number(form.get("ttl_days") ?? "7") || 7;

  if (!wsId) return NextResponse.redirect(new URL("/members", origin), 303);
  if (!isApiConfigured())
    return back(origin, "api_unavailable");
  if (!emails.trim())
    return back(origin, "empty");

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
    const url = new URL("/members", origin);
    url.searchParams.set("invited", String(created.length));
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
      if (err.status === 422) return back(origin, "bad_input");
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
