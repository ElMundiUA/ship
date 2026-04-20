/**
 * Answer / skip / reopen a clarification (C9).
 *
 * Used by the ``/clarifications`` page's inline forms. Accepts
 * ``action ∈ {answer, skip, reopen}`` plus ``answer`` (for
 * ``answer``) and redirects back to the list with a status banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateClarification,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const id = (form.get("id") ?? "").toString();
  const action = (form.get("action") ?? "answer").toString();
  const answer = (form.get("answer") ?? "").toString().trim();
  const statusFilter = (form.get("status_filter") ?? "").toString();

  if (!wsId || !id) return back(origin, "missing_args", statusFilter);
  if (!isApiConfigured()) return back(origin, "api_unavailable", statusFilter);

  let patch: Parameters<typeof updateClarification>[2];
  if (action === "skip") patch = { status: "skipped" };
  else if (action === "reopen") patch = { status: "open" };
  else {
    if (!answer) return back(origin, "empty_answer", statusFilter, id);
    patch = { answer, status: "answered" };
  }

  try {
    await updateClarification(wsId, id, patch);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, "api_unavailable", statusFilter);
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?next=%2Fclarifications", origin),
          303,
        );
      if (err.status === 404) return back(origin, "not_found", statusFilter);
      if (err.status === 422) return back(origin, "bad_input", statusFilter, id);
      return back(origin, `http_${err.status}`, statusFilter);
    }
    return back(origin, "unknown", statusFilter);
  }

  const done = action === "skip" ? "skipped" : action === "reopen" ? "reopened" : "answered";
  return back(origin, done, statusFilter);
}

function back(origin: string, reason: string, statusFilter?: string, id?: string) {
  const url = new URL("/clarifications", origin);
  if (statusFilter) url.searchParams.set("status", statusFilter);
  url.searchParams.set("banner", reason);
  if (id) url.searchParams.set("focus", id);
  return NextResponse.redirect(url, 303);
}
