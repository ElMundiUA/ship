/**
 * POST /api/inbox/[id]/unsnooze — wake a snoozed inbox item.
 *
 * No body fields beyond ``ws``. Forwards to ``POST /v1/.../inbox/{id}/unsnooze``
 * and bounces back to the detail page.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  unsnoozeInboxItem,
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

  if (!wsId || !id) return back(origin, id, "bad_input");
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  try {
    await unsnoozeInboxItem(wsId, id);
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return back(origin, id, codeFor(err));
  }

  return NextResponse.redirect(new URL(`/inbox?selected=${id}`, origin), 303);
}

function back(origin: string, id: string, code: string) {
  const url = new URL(`/inbox?selected=${id}`, origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 422) return "state_invalid";
    return `http_${err.status}`;
  }
  return "unknown";
}
