/**
 * Onboarding step — reuse a GitHub App install from a sibling workspace.
 *
 * When the Ship GitHub App is already installed on an account but this
 * workspace has no ``GitHubInstallation`` row, redirecting to GitHub's
 * configure screen leaves Save disabled. This route binds the existing
 * install in Ship without a GitHub round-trip.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  linkGitHubInstallation,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const rawInstallId = (form.get("installation_id") ?? "").toString().trim();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  const installationId = Number.parseInt(rawInstallId, 10);
  if (!Number.isFinite(installationId) || installationId <= 0) {
    return wizardError(origin, wsId, "bad_installation");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  try {
    await linkGitHubInstallation(wsId, { installation_id: installationId });
    const url = new URL("/onboarding", origin);
    url.searchParams.set("step", "repos");
    url.searchParams.set("ws", wsId);
    url.searchParams.set("github", "installed");
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403)
        return wizardError(origin, wsId, "forbidden");
      if (err.status === 404)
        return wizardError(origin, wsId, "installation_not_found");
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
