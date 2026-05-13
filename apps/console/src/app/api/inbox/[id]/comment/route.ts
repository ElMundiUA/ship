/**
 * POST /api/inbox/[id]/comment — append a free-text comment event to
 * an inbox item.
 *
 * Forwards to ``POST /v1/.../inbox/{id}/events`` with a minimal
 * ``{body, payload: {}}`` envelope. The new event will appear on the
 * timeline on the next page load (the backend orders events
 * ascending by ``created_at``).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  appendInboxEvent,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const origin = resolveOrigin(request);
  const { id } = await params;
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const body = (form.get("body") ?? "").toString().trim();

  if (!wsId || !id) return back(origin, id, "bad_input");
  if (!body) return back(origin, id, "validation_failed");
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  try {
    await appendInboxEvent(wsId, id, { body, payload: {} });
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return back(origin, id, codeFor(err));
  }

  return NextResponse.redirect(new URL(`/inbox/${id}`, origin), 303);
}

function back(origin: string, id: string, code: string) {
  const url = new URL(`/inbox/${id}`, origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
