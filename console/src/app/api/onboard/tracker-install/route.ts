/**
 * Onboarding step — kick off Linear or Notion OAuth, or skip the
 * tracker setup entirely.
 *
 * Form submits with `kind=linear|notion|github|skip`:
 *
 * - `linear` / `notion` — POST to backend `install/start` for the
 *   chosen vendor, then 303-redirect to the returned `install_url`.
 *   The vendor sends the user to its own OAuth approval page; the
 *   backend's `install/callback` handles persistence and bounces them
 *   back into `?step=knowledge` on the console origin.
 * - `github` — no extra OAuth needed; the GitHub App installed on the
 *   GitHub step already grants Issues access. We just bounce forward
 *   to the next wizard step.
 * - `skip` — same forward bounce, no API calls.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  startLinearInstall,
  startNotionInstall,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repo = (form.get("repo") ?? "").toString();
  const kind = (form.get("kind") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (kind === "skip" || kind === "github") {
    // Both the skip and "use GitHub Issues" branches are no-ops on the
    // console side — we just keep moving forward in the wizard. The
    // GitHub App install step is a hard prerequisite for either, so
    // there's nothing else to wire here.
    return forward(origin, wsId, repo);
  }

  if (kind !== "linear" && kind !== "notion") {
    return wizardError(origin, wsId, repo, "bad_kind");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, repo, "api_unavailable");
  }

  try {
    const start =
      kind === "linear"
        ? await startLinearInstall(wsId)
        : await startNotionInstall(wsId);
    return NextResponse.redirect(start.install_url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, repo, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403)
        return wizardError(origin, wsId, repo, "forbidden");
      // 503 = backend says the OAuth app is not configured (missing
      // {LINEAR,NOTION}_CLIENT_ID / SECRET).
      if (err.status === 503)
        return wizardError(origin, wsId, repo, "not_configured");
      return wizardError(origin, wsId, repo, `http_${err.status}`);
    }
    return wizardError(origin, wsId, repo, "unknown");
  }
}

function forward(origin: string, wsId: string, repo: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "knowledge");
  url.searchParams.set("ws", wsId);
  if (repo) url.searchParams.set("repo", repo);
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, repo: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "tracker");
  url.searchParams.set("ws", wsId);
  if (repo) url.searchParams.set("repo", repo);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
