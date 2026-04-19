/**
 * Onboarding step 2 — optional first integration.
 *
 * Accepts a "kind" + arbitrary scalar fields under `config_*` plus a single
 * `secret` field. We strip the `config_` prefix before forwarding so the form
 * markup stays simple. Submitting with the `skip` button just advances the
 * wizard without touching the API.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  upsertIntegration,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const ALLOWED_KINDS = new Set([
  "linear",
  "jira",
  "github",
  "gitlab",
  "slack",
  "teams",
  "otel",
  "webhook",
  "s3-export",
]);

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repo = (form.get("repo") ?? "").toString();
  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  // "Skip" button posts with `intent=skip` — don't even call the API.
  if (form.get("intent") === "skip") {
    return advance(origin, wsId, repo);
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, repo, "api_unavailable");
  }

  const kind = (form.get("kind") ?? "").toString();
  if (!ALLOWED_KINDS.has(kind)) {
    return wizardError(origin, wsId, repo, "bad_kind");
  }

  const secret = (form.get("secret") ?? "").toString();
  if (!secret) {
    return wizardError(origin, wsId, repo, "missing_secret");
  }

  // Promote `config_<key>=value` form fields into a structured config blob,
  // skipping empty values so we don't store noise like {"team_id": ""}.
  const config: Record<string, string> = {};
  for (const [key, value] of form.entries()) {
    if (!key.startsWith("config_")) continue;
    const k = key.slice("config_".length);
    const v = value.toString().trim();
    if (k && v) config[k] = v;
  }

  try {
    await upsertIntegration(wsId, kind, { config, secret });
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, repo, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return wizardError(origin, wsId, repo, "forbidden");
      return wizardError(origin, wsId, repo, `http_${err.status}`);
    }
    return wizardError(origin, wsId, repo, "unknown");
  }

  return advance(origin, wsId, repo);
}

function advance(origin: string, wsId: string, repo: string) {
  const url = new URL("/onboarding", origin);
  // If we have a repo to seed knowledge from, take that detour first;
  // otherwise jump straight to minting the CLI token.
  url.searchParams.set("step", repo ? "knowledge" : "token");
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
