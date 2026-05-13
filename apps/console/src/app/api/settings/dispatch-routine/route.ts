/**
 * POST /api/settings/dispatch-routine — manually fire a Ship routine
 * against the workspace's selected repo. Forwards to
 * ``POST /v1/workspaces/{ws}/agent-runs/dispatch`` (PR-6 of the
 * local-CLI swap), which dispatches ``ship-trigger-schedule.yml``
 * with the routine + optional ticket inputs.
 *
 * Server-action endpoint for the General settings "Dispatch routine"
 * card. Form fields: ``ws``, ``repo``, ``routine``, optional ``ticket``.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  dispatchRoutine,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const ROUTINE_RE = /^[a-z0-9_-]{1,64}$/i;
const TICKET_RE = /^[A-Z0-9-]{1,32}$/;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo") ?? "").toString();
  const routine = (form.get("routine") ?? "").toString().trim();
  const ticket = (form.get("ticket") ?? "").toString().trim();

  if (!UUID_RE.test(wsId) || !UUID_RE.test(repoId) || !ROUTINE_RE.test(routine)) {
    return back(origin, "bad_input");
  }
  if (ticket && !TICKET_RE.test(ticket)) {
    return back(origin, "bad_ticket");
  }
  if (!isApiConfigured()) {
    return back(origin, "api_unavailable");
  }

  try {
    await dispatchRoutine(wsId, {
      repo_id: repoId,
      routine_id: routine,
      ticket_ref: ticket || null,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, "forbidden");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  const url = new URL("/settings/general", origin);
  url.searchParams.set("dispatched", routine);
  return NextResponse.redirect(url, 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings/general", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
