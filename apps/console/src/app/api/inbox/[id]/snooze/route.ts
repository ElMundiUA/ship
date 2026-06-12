/**
 * POST /api/inbox/[id]/snooze — silence an inbox item until a future
 * timestamp (≤ 30 days out).
 *
 * Accepts ``snoozed_until`` from the form (the value of a native
 * ``<input type="datetime-local">`` — ISO-ish but without a timezone
 * suffix), coerces it to RFC 3339 with the operator's local offset
 * folded in, and forwards to ``POST /v1/.../inbox/{id}/snooze``.
 * Bounces back to the detail page on failure with an ``?error=``
 * code.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  snoozeInboxItem,
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
  const raw = (form.get("snoozed_until") ?? "").toString().trim();

  if (!wsId || !id || !raw) return back(origin, id, "bad_input");
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  // datetime-local emits "YYYY-MM-DDTHH:MM" (no seconds, no zone) on
  // most browsers and "YYYY-MM-DDTHH:MM:SS" on a few. ``new Date``
  // interprets that string in the operator's local timezone, which
  // is exactly what they meant when they typed it. We then emit a
  // fully-qualified ISO 8601 string so the backend's ``datetime``
  // parser locks the offset down before it does any math.
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return back(origin, id, "bad_input");
  }
  const iso = parsed.toISOString();

  try {
    await snoozeInboxItem(wsId, id, iso);
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
      // Tease apart the two snooze-specific 422s so the page can
      // render a precise hint instead of a generic "validation
      // failed". Backend phrases are stable: see
      // backend/app/api/v1/routes/inbox.py.
      const detail =
        typeof err.detail === "string" ? err.detail.toLowerCase() : "";
      if (detail.includes("must be in the future")) return "snooze_in_past";
      if (detail.includes("snooze cap")) return "snooze_too_far";
      return "validation_failed";
    }
    return `http_${err.status}`;
  }
  return "unknown";
}
