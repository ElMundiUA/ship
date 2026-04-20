/**
 * Dashboard form handler — "Install everything" multi-preset bundle PR.
 *
 * Single entry point for the WOW onboarding promise: click once, get
 * one PR in the customer repo that adds every workflow the preset(s)
 * asked for plus ``.ship/config.yml``. On merge the knowledge-lane
 * auto-dispatch (see ``auto_dispatch_knowledge_pipelines`` on the
 * backend) hydrates the dashboard before the user's second visit.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  installBundle,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  // Optional comma/space-separated override. If empty we let the
  // backend fall back to the repo's persisted preset.
  const rawPresets = (form.get("presets") ?? "").toString();
  const presets = rawPresets
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  if (!wsId || !repoId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, repoId, "api_unavailable");
  }

  try {
    const result = await installBundle(wsId, repoId, {
      presets: presets.length > 0 ? presets : undefined,
    });
    if (result.pr_url) {
      return NextResponse.redirect(result.pr_url, 303);
    }
    return back(origin, wsId, repoId, "bundle_installed");
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, repoId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, repoId, "forbidden");
      if (err.status === 404) return back(origin, wsId, repoId, "missing");
      if (err.status === 412) {
        const code =
          err.detail && typeof err.detail === "object" && "code" in err.detail
            ? String((err.detail as { code: unknown }).code)
            : "precondition";
        return back(origin, wsId, repoId, `bundle_${code}`);
      }
      if (err.status === 422) {
        return back(origin, wsId, repoId, "bundle_invalid_preset");
      }
      if (err.status === 502) {
        const detailMsg =
          err.detail && typeof err.detail === "object" && "message" in err.detail
            ? String((err.detail as { message?: unknown }).message ?? "").slice(
                0,
                240,
              )
            : undefined;
        return back(origin, wsId, repoId, "bundle_upstream", detailMsg);
      }
      return back(origin, wsId, repoId, `http_${err.status}`);
    }
    return back(origin, wsId, repoId, "unknown");
  }
}

function back(
  origin: string,
  wsId: string,
  repoId: string,
  reason: string,
  detail?: string,
) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("installed", "bundle");
  url.searchParams.set("repo_id", repoId);
  url.searchParams.set("reason", reason);
  if (detail) url.searchParams.set("detail", detail);
  return NextResponse.redirect(url, 303);
}
