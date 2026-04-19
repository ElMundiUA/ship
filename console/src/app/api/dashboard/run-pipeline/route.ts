/**
 * Dashboard form handler — execute a "Run now" on a pipeline.
 *
 * The dashboard's pipeline cards each render a tiny form (one hidden
 * ``ws`` field + the pipeline id) that POSTs here. We forward to the
 * backend and bounce back to the dashboard with a banner reason so the
 * user sees a confirmation toast on the next render.
 *
 * Form-driven (no fetch in the browser) keeps the dashboard usable
 * without JS and keeps the session token in the httpOnly cookie.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  runPipeline,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const pipelineId = (form.get("pipeline") ?? "").toString();
  const note = (form.get("note") ?? "").toString().trim() || undefined;

  if (!wsId || !pipelineId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, pipelineId, "api_unavailable");
  }

  try {
    await runPipeline(wsId, pipelineId, note);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, pipelineId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403)
        return back(origin, wsId, pipelineId, "forbidden");
      if (err.status === 404)
        return back(origin, wsId, pipelineId, "missing");
      if (err.status === 409)
        return back(origin, wsId, pipelineId, "disabled");
      return back(origin, wsId, pipelineId, `http_${err.status}`);
    }
    return back(origin, wsId, pipelineId, "unknown");
  }

  return back(origin, wsId, pipelineId, "ran");
}

function back(origin: string, wsId: string, pipelineId: string, reason: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("ran", pipelineId);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}
