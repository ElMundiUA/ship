/**
 * POST /api/inbox/[id]/discuss — open a Navigator chat thread seeded
 * with this inbox item's context.
 *
 * Server-action endpoint for the "Discuss with Navigator" button on
 * the inbox detail page. Forwards to
 * ``POST /v1/workspaces/{ws}/inbox/{id}/discuss`` and redirects the
 * browser to ``/chat?from_inbox=<item_id>`` so the chat surface can
 * render a "linked clarification" banner. Failures bounce back to
 * the inbox detail page with an ``?error=`` code.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  discussInboxItemWithNavigator,
  isApiConfigured,
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
  if (!wsId || !id) return back(origin, id, "bad_input");
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  try {
    const out = await discussInboxItemWithNavigator(wsId, id);
    return NextResponse.redirect(
      new URL(`/chat?from_inbox=${encodeURIComponent(out.inbox_item_id)}`, origin),
      303,
    );
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, id, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401 || err.status === 403)
        return back(origin, id, "forbidden");
      if (err.status === 404) return back(origin, id, "not_found");
    }
    return back(origin, id, "internal");
  }
}

function back(origin: string, id: string, code: string) {
  const url = new URL(inboxItemUrl(id), origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
