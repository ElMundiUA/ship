/**
 * Dashboard form handler — open the current wizard seed PR.
 *
 * This route keeps the old form action stable, but intentionally calls
 * ``wizard_seed`` rather than legacy ``install_bundle``. Bundle drift
 * means "re-run the current seed composer", not "re-open the old
 * workflow-only PR".
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  wizardSeed,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";
import { getSessionToken } from "@/lib/api/session";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();

  if (!wsId || !repoId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, repoId, "api_unavailable");
  }

  try {
    const token = (await getSessionToken()) ?? undefined;
    const result = await wizardSeed(wsId, repoId, {}, token);
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
