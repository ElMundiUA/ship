/**
 * Create a new chat thread (C10).
 *
 * Renders the "new thread" composer on ``/chat``. Takes ``title``
 * + ``initial_message`` + optional ``repo_id`` / ``workflow_id`` and
 * redirects to ``/chat/{thread_id}`` on success so the detail page
 * renders with the server's first assistant reply in place.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createChatThread,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const title = (form.get("title") ?? "").toString().trim();
  const initial = (form.get("initial_message") ?? "").toString().trim();
  const repoId = (form.get("repo_id") ?? "").toString().trim() || null;
  const workflowId = (form.get("workflow_id") ?? "").toString().trim() || null;

  if (!wsId) return back(origin, "missing_args");
  if (!isApiConfigured()) return back(origin, "api_unavailable");
  if (!title || !initial) return back(origin, "empty");

  try {
    const thread = await createChatThread(wsId, {
      title,
      initial_message: initial,
      repo_id: repoId,
      workflow_id: workflowId,
    });
    return NextResponse.redirect(
      new URL(`/chat/${encodeURIComponent(thread.id)}`, origin),
      303,
    );
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?next=%2Fchat", origin),
          303,
        );
      if (err.status === 422) return back(origin, "bad_input");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }
}

function back(origin: string, reason: string) {
  const url = new URL("/chat", origin);
  url.searchParams.set("banner", reason);
  return NextResponse.redirect(url, 303);
}
