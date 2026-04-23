/**
 * POST /api/inbox/[id]/disposition — drive an inbox item through its
 * lifecycle (resolve / dismiss / approve / reject / answer / accept /
 * retry / acknowledge).
 *
 * Server-action endpoint for the /inbox/[id] page's primary + secondary
 * disposition buttons (and the clarification "Answer" form). Forwards
 * the form payload to ``POST /v1/workspaces/{ws}/inbox/{id}/disposition``
 * and bounces back to the detail page with an ``?error=`` code on
 * failure so the server component can render a banner.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  applyInboxDisposition,
  isApiConfigured,
  type InboxDispositionAction,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const VALID_ACTIONS: readonly InboxDispositionAction[] = [
  "resolve",
  "dismiss",
  "approve",
  "reject",
  "answer",
  "accept",
  "retry",
  "acknowledge",
];

export async function POST(
  request: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const origin = resolveOrigin(request);
  const { id } = await params;
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const actionRaw = (form.get("action") ?? "").toString();
  const answer = (form.get("answer") ?? "").toString().trim();
  const payloadJsonRaw = (form.get("payload_json") ?? "").toString().trim();

  if (!wsId || !id) return back(origin, id, "bad_input");
  if (!VALID_ACTIONS.includes(actionRaw as InboxDispositionAction)) {
    return back(origin, id, "bad_input");
  }
  const action = actionRaw as InboxDispositionAction;
  if (!isApiConfigured()) return back(origin, id, "api_unavailable");

  // Build the payload bag. ``payload_json`` (advanced operators)
  // merges underneath ``answer`` so the dedicated text field always
  // wins for clarifications.
  let payload: Record<string, unknown> = {};
  if (payloadJsonRaw) {
    try {
      const parsed = JSON.parse(payloadJsonRaw);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        payload = { ...(parsed as Record<string, unknown>) };
      } else {
        return back(origin, id, "bad_input");
      }
    } catch {
      return back(origin, id, "bad_input");
    }
  }

  const body: Parameters<typeof applyInboxDisposition>[2] = {
    action,
    payload,
  };

  if (action === "answer") {
    if (!answer) return back(origin, id, "validation_failed");
    body.answer = answer;
    body.payload = { ...payload, answer };
  }

  try {
    await applyInboxDisposition(wsId, id, body);
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
    if (err.status === 409) return "state_invalid";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
