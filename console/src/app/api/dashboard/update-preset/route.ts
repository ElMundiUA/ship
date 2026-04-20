/**
 * Dashboard form handler — change the preset bound to a repo (B9).
 *
 * The UI posts ``ws``, ``repo_id``, ``preset``, and an optional
 * ``reshape`` checkbox. On success we bounce back to the dashboard
 * with a banner. Unknown-preset (422) gets a dedicated reason so
 * the banner nudges the operator at the right thing.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateRepo,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const preset = (form.get("preset") ?? "").toString() || null;
  const reshape = form.get("reshape") === "on";

  if (!wsId || !repoId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, "api_unavailable");
  }

  try {
    const result = await updateRepo(wsId, repoId, { preset, reshape });
    const detail = `${result.full_name} → ${result.preset ?? "default"}${
      reshape ? " · lanes reshaped" : ""
    }`;
    return back(origin, wsId, "preset_updated", detail);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "preset_missing_repo");
      if (err.status === 422) return back(origin, wsId, "preset_invalid");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }
}

function back(origin: string, wsId: string, reason: string, detail?: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("preset", "1");
  url.searchParams.set("reason", reason);
  if (detail) url.searchParams.set("detail", detail);
  return NextResponse.redirect(url, 303);
}
