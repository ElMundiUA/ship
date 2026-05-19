/**
 * POST /api/inbox/[id]/decide — structured operator decision on a row
 * carrying ``payload.action_items`` (ELS-159 / ELS-145 per-item binary).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  decideInboxItem,
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
  const actionItemId = (form.get("action_item_id") ?? "").toString().trim();
  const choiceRaw = (form.get("choice") ?? "").toString().trim();
  const returnToRaw = (form.get("return_to") ?? "").toString();
  const returnTo =
    returnToRaw.startsWith("/") && !returnToRaw.startsWith("//")
      ? returnToRaw
      : null;

  if (!wsId || !id) return back(origin, id, "bad_input", returnTo);
  if (!isApiConfigured()) return back(origin, id, "api_unavailable", returnTo);

  if (actionItemId) {
    if (choiceRaw !== "primary" && choiceRaw !== "secondary") {
      return back(origin, id, "bad_input", returnTo);
    }
    try {
      await decideInboxItem(wsId, id, {
        action_item_id: actionItemId,
        choice: choiceRaw,
      });
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return back(origin, id, codeFor(err), returnTo);
    }
    const successTarget = returnTo ?? `/inbox/${id}`;
    return NextResponse.redirect(new URL(successTarget, origin), 303);
  }

  return back(origin, id, "bad_input", returnTo);
}

function back(origin: string, id: string, code: string, returnTo: string | null) {
  const url = new URL(returnTo ?? `/inbox/${id}`, origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 409) return "state_invalid";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
