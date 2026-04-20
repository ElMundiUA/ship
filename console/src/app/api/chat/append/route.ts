/**
 * Append a message to an existing chat thread (C10).
 *
 * Bounces back to ``/chat/{thread_id}`` so the server-rendered
 * detail page re-loads with both the user message and the stub
 * assistant reply already present.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  appendChatMessage,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const threadId = (form.get("thread_id") ?? "").toString();
  const body = (form.get("body") ?? "").toString().trim();

  if (!wsId || !threadId) return fallback(origin, "missing_args");
  if (!isApiConfigured()) return thread(origin, threadId, "api_unavailable");
  if (!body) return thread(origin, threadId, "empty");

  try {
    await appendChatMessage(wsId, threadId, body);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return thread(origin, threadId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL(`/login?next=%2Fchat%2F${threadId}`, origin),
          303,
        );
      if (err.status === 422) return thread(origin, threadId, "closed");
      if (err.status === 404) return fallback(origin, "not_found");
      return thread(origin, threadId, `http_${err.status}`);
    }
    return thread(origin, threadId, "unknown");
  }

  return NextResponse.redirect(
    new URL(`/chat/${encodeURIComponent(threadId)}`, origin),
    303,
  );
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
