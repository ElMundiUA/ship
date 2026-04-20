/**
 * Decide on an improvement (C8).
 *
 * Accepts ``decision ∈ {accepted,declined,deferred,pending}`` plus
 * optional ``decision_reason`` / ``next_action_url`` and redirects
 * back to ``/improvements`` with a banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  type ApiImprovementDecision,
  isApiConfigured,
  updateImprovement,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const VALID: ApiImprovementDecision[] = [
  "accepted",
  "declined",
  "deferred",
  "pending",
];

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const id = (form.get("id") ?? "").toString();
  const decision = (form.get("decision") ?? "").toString() as ApiImprovementDecision;
  const reason = (form.get("reason") ?? "").toString().trim();
  const nextUrl = (form.get("next_action_url") ?? "").toString().trim();
  const decisionFilter = (form.get("decision_filter") ?? "").toString();

  if (!wsId || !id || !VALID.includes(decision))
    return back(origin, "missing_args", decisionFilter);
  if (!isApiConfigured()) return back(origin, "api_unavailable", decisionFilter);
  if (decision === "declined" && !reason)
    return back(origin, "reason_required", decisionFilter, id);

  try {
    await updateImprovement(wsId, id, {
      decision,
      decision_reason: reason || undefined,
      next_action_url: nextUrl || undefined,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, "api_unavailable", decisionFilter);
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?next=%2Fimprovements", origin),
          303,
        );
      if (err.status === 404) return back(origin, "not_found", decisionFilter);
      if (err.status === 422) return back(origin, "bad_input", decisionFilter, id);
      return back(origin, `http_${err.status}`, decisionFilter);
    }
    return back(origin, "unknown", decisionFilter);
  }

  return back(origin, `decided_${decision}`, decisionFilter);
}

function back(
  origin: string,
  reason: string,
  decisionFilter?: string,
  id?: string,
) {
  const url = new URL("/improvements", origin);
  if (decisionFilter) url.searchParams.set("decision", decisionFilter);
  url.searchParams.set("banner", reason);
  if (id) url.searchParams.set("focus", id);
  return NextResponse.redirect(url, 303);
}
