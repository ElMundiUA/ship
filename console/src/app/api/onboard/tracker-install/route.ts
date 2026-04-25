/**
 * Onboarding step — connect a tracker/provider or skip setup entirely.
 *
 * Form submits with
 * `kind=linear|notion|atlassian|azure_devops|gitlab|github|skip`:
 *
 * - `linear` / `notion` — POST to backend `install/start` for the chosen
 *   vendor, then 303-redirect to the returned `install_url`. The vendor
 *   sends the user to its own OAuth approval page; the backend's
 *   `install/callback` handles persistence and bounces them back into
 *   `?step=done` on the console origin.
 * - `atlassian` / `azure_devops` / `gitlab` — PAT/API-token-backed native
 *   provider installs.
 * - `github` — no extra OAuth needed; the GitHub App installed on the
 *   first wizard step already grants Issues access. We just bounce
 *   forward to the done step.
 * - `skip` — same forward bounce, no API calls.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  connectAzureDevOpsPat,
  connectAtlassianApiToken,
  connectGitLabPat,
  isApiConfigured,
  startLinearInstall,
  startNotionInstall,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const kind = (form.get("kind") ?? "").toString();

  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (kind === "skip" || kind === "github") {
    return forward(origin, wsId);
  }

  if (
    kind !== "linear" &&
    kind !== "notion" &&
    kind !== "atlassian" &&
    kind !== "azure_devops" &&
    kind !== "gitlab"
  ) {
    return wizardError(origin, wsId, "bad_kind");
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, "api_unavailable");
  }

  try {
    if (kind === "atlassian") {
      const site = (form.get("site") ?? "").toString().trim();
      const email = (form.get("email") ?? "").toString().trim();
      const apiToken = (form.get("api_token") ?? "").toString();
      const jiraProject = (form.get("jira_project") ?? "").toString().trim();
      if (!site || !email || !apiToken) {
        return wizardError(origin, wsId, "bad_atlassian_input");
      }
      await connectAtlassianApiToken(wsId, {
        site,
        email,
        api_token: apiToken,
        jira_project: jiraProject || null,
      });
      return forward(origin, wsId, { atlassian: "connected" });
    }
    if (kind === "azure_devops") {
      const organization = (form.get("organization") ?? "").toString().trim();
      const project = (form.get("project") ?? "").toString().trim();
      const pat = (form.get("pat") ?? "").toString();
      if (!organization || !pat) {
        return wizardError(origin, wsId, "bad_azure_devops_input");
      }
      await connectAzureDevOpsPat(wsId, {
        organization,
        project: project || null,
        pat,
        scopes: ["vso.code", "vso.build_execute"],
      });
      return forward(origin, wsId, { azure_devops: "connected" });
    }
    if (kind === "gitlab") {
      const host = (form.get("host") ?? "").toString().trim();
      const group = (form.get("group") ?? "").toString().trim();
      const pat = (form.get("pat") ?? "").toString();
      if (!host || !pat) {
        return wizardError(origin, wsId, "bad_gitlab_input");
      }
      await connectGitLabPat(wsId, {
        host,
        group: group || null,
        pat,
        scopes: ["read_api", "read_repository"],
      });
      return forward(origin, wsId, { gitlab: "connected" });
    }
    const start =
      kind === "linear"
        ? await startLinearInstall(wsId)
        : await startNotionInstall(wsId);
    return NextResponse.redirect(start.install_url, 303);
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
      // 503 = backend says the OAuth app is not configured (missing
      // {LINEAR,NOTION}_CLIENT_ID / SECRET). We embed the kind so the
      // wizard banner can call out which vendor needs ops attention
      // (and that GitHub Issues remains a viable alternative).
      if (err.status === 503)
        return wizardError(origin, wsId, `not_configured_${kind}`);
      return wizardError(origin, wsId, `http_${err.status}`);
    }
    return wizardError(origin, wsId, "unknown");
  }
}

function forward(
  origin: string,
  wsId: string,
  extra?: Record<string, string>,
) {
  // Wave-8c: after the workspace-level tracker OAuth, drop the user
  // into the per-repo Confirm bootstrap step. That's where they
  // review the canonical Plays bundle, bind a tracker / push agent
  // secrets per repo and open the unified seed PR.
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "confirm");
  url.searchParams.set("ws", wsId);
  for (const [key, value] of Object.entries(extra ?? {})) {
    url.searchParams.set(key, value);
  }
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "tracker");
  url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
