/**
 * Resolve or archive a chat thread (C10).
 *
 * ``action`` is either ``resolved`` (expects ``ticket_ref`` and
 * optionally creates an Improvement) or ``archived`` (no ticket,
 * no Improvement). Redirects back to ``/chat`` either way so the
 * list page shows the thread's terminal status.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  resolveChatThread,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const threadId = (form.get("thread_id") ?? "").toString();
  const action =
    (form.get("action") ?? "resolved").toString() === "archived"
      ? "archived"
      : "resolved";
  const ticketRef = (form.get("ticket_ref") ?? "").toString().trim();
  const createImprovement = form.get("create_improvement") === "on";

  if (!wsId || !threadId) return fallback(origin, "missing_args");
  if (!isApiConfigured()) return thread(origin, threadId, "api_unavailable");
  if (action === "resolved" && !ticketRef)
    return thread(origin, threadId, "ticket_required");

  try {
    await resolveChatThread(wsId, threadId, {
      ticket_ref: ticketRef || "archived",
      create_improvement: createImprovement,
      action,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return thread(origin, threadId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL(`/login?next=%2Fchat%2F${threadId}`, origin),
          303,
        );
      if (err.status === 404) return fallback(origin, "not_found");
      if (err.status === 422) return thread(origin, threadId, "already_closed");
      return thread(origin, threadId, `http_${err.status}`);
    }
    return thread(origin, threadId, "unknown");
  }

  const url = new URL("/chat", origin);
  url.searchParams.set("banner", action === "archived" ? "archived" : "resolved");
  return NextResponse.redirect(url, 303);
}

function thread(origin: string, threadId: string, reason: string) {
  const url = new URL(`/chat/${encodeURIComponent(threadId)}`, origin);
  url.searchParams.set("banner", reason);
  return NextResponse.redirect(url, 303);
}

function fallback(origin: string, reason: string) {
  const url = new URL("/chat", origin);
  url.searchParams.set("banner", reason);
  return NextResponse.redirect(url, 303);
}
