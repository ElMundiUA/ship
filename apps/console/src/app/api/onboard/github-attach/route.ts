/**
 * Onboarding step — attach an *existing* GitHub App installation to
 * this workspace instead of redirecting through GitHub's install picker.
 *
 * Triggered by the "Use @acme" CTA on step-1 when the user already has
 * Ship installed somewhere they can reach. We:
 *
 * 1. POST to the backend `/integrations/github/install/attach` with the
 *    chosen `installation_id` + the target workspace.
 * 2. 303-redirect the browser straight into the wizard's repo picker —
 *    same destination GitHub's callback would land them on after a
 *    fresh install. That keeps the two branches indistinguishable for
 *    every downstream step.
 *
 * Mirrors the shape of `github-install/route.ts` (POST form → redirect)
 * so the wizard can use a plain `<form>` without any client JS.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  attachGitHubInstallation,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const installationRaw = (form.get("installation_id") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }
  const installationId = Number.parseInt(installationRaw, 10);
  if (!Number.isFinite(installationId) || installationId <= 0) {
    return wizardError(origin, wsId, "bad_installation");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  try {
    await attachGitHubInstallation(wsId, installationId);
    // Same landing as the GitHub redirect path so the wizard doesn't
    // need a separate "you attached" success state.
    const next = new URL("/onboarding", origin);
    next.searchParams.set("step", "repos");
    next.searchParams.set("ws", wsId);
    next.searchParams.set("github", "installed");
    return NextResponse.redirect(next, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return wizardError(origin, wsId, "forbidden");
      if (err.status === 404)
        return wizardError(origin, wsId, "installation_not_accessible");
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }
}

function wizardError(origin: string, wsId: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "github");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
