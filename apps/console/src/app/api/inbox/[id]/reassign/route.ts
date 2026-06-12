/**
 * POST /api/inbox/[id]/reassign — change the owner of an inbox item.
 *
 * Two mutually-exclusive modes:
 *   - ``user_id`` — pin to a specific workspace member
 *   - ``handle`` — re-resolve via the routing service
 *
 * Exactly one must be non-empty (the backend would also enforce this
 * but rejecting client-side keeps the round-trip cheap and the error
 * code precise). Forwards to ``POST /v1/.../inbox/{id}/reassign``.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  reassignInboxItem,
} from "@/lib/api/client";
import { inboxItemUrl } from "@/components/inbox/inbox-url";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const origin = resolveOrigin(request);
  const { id } = await params;
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const userId = (form.get("user_id") ?? "").toString().trim();
  const handle = (form.get("handle") ?? "").toString().trim();

  if (!wsId || !id) return back(origin, id, "bad_input");
  // XOR: exactly one of user_id / handle must be present. Both empty
  // means the form was submitted blank; both filled means the
  // operator picked a member AND typed a handle (likely a UI bug we
  // want to surface immediately rather than letting the backend
  // 422 with a less specific phrase).
  if ((userId.length === 0) === (handle.length === 0)) {
    return back(origin, id, "bad_input");
  }
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  const body: Parameters<typeof reassignInboxItem>[2] =
    userId.length > 0 ? { user_id: userId } : { handle };

  try {
    await reassignInboxItem(wsId, id, body);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return back(origin, id, codeFor(err));
  }

  return NextResponse.redirect(new URL(inboxItemUrl(id), origin), 303);
}

function back(origin: string, id: string, code: string) {
  const url = new URL(inboxItemUrl(id), origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 422) {
      const detail =
        typeof err.detail === "string" ? err.detail.toLowerCase() : "";
      if (detail.includes("handle resolved to no user")) {
        return "handle_unresolved";
      }
      return "validation_failed";
    }
    return `http_${err.status}`;
  }
  return "unknown";
}
