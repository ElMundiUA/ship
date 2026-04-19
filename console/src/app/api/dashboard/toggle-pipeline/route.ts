/**
 * Dashboard form handler — toggle a pipeline on/off.
 *
 * Form sends ``ws``, ``pipeline``, and ``enabled=on|off``. We translate
 * to the backend PATCH and bounce back to the dashboard. Cards are
 * laid out so the toggle's label always reflects the new state, so the
 * user gets immediate feedback even on slow networks.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  togglePipeline,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const pipelineId = (form.get("pipeline") ?? "").toString();
  const enabledRaw = (form.get("enabled") ?? "").toString();
  const enabled = enabledRaw === "on" || enabledRaw === "true";

  if (!wsId || !pipelineId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, pipelineId, "api_unavailable");
  }

  try {
    await togglePipeline(wsId, pipelineId, enabled);
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
      return back(origin, wsId, pipelineId, `http_${err.status}`);
    }
    return back(origin, wsId, pipelineId, "unknown");
  }

  return back(origin, wsId, pipelineId, enabled ? "enabled" : "disabled");
}

function back(origin: string, wsId: string, pipelineId: string, reason: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("toggled", pipelineId);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}
