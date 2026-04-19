/**
 * Onboarding step 1 — inspect a repo URL or local path.
 *
 * The wizard POSTs the source as a native form, we call the backend, and
 * redirect to the workspace step with the suggested name + slug pre-filled
 * (and the repo source carried along so later steps can hand it back to
 * the installer / seeder).
 *
 * "Use demo repo" buttons send `intent=demo`, which asks the backend to
 * scaffold a fixture repo on its own filesystem and uses the resulting
 * `file://` path as the source.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  inspectRepo,
  isApiConfigured,
  scaffoldDemoRepo,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  if (!isApiConfigured()) {
    return wizardError(origin, "api_unavailable", "");
  }

  const form = await request.formData();
  const intent = (form.get("intent") ?? "").toString();

  let source = (form.get("source") ?? "").toString().trim();
  if (intent === "demo") {
    try {
      const demo = await scaffoldDemoRepo();
      source = demo.suggestion;
    } catch (err) {
      if (err instanceof ApiHttpError && err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      return wizardError(origin, "demo_failed", source);
    }
  }

  if (!source) {
    return wizardError(origin, "missing_source", "");
  }

  try {
    const profile = await inspectRepo(source);
    const next = new URL("/onboarding", origin);
    next.searchParams.set("step", "workspace");
    next.searchParams.set("repo", profile.source);
    next.searchParams.set("name", profile.suggested_name);
    next.searchParams.set("slug", profile.suggested_slug);
    return NextResponse.redirect(next, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return wizardError(origin, "api_unavailable", source);
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      const code =
        typeof err.detail === "object" &&
        err.detail !== null &&
        "code" in err.detail
          ? String((err.detail as { code: unknown }).code)
          : `http_${err.status}`;
      return wizardError(origin, code, source);
    }
    return wizardError(origin, "unknown", source);
  }
}

function wizardError(origin: string, code: string, source: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "repo");
  url.searchParams.set("error", code);
  if (source) url.searchParams.set("source", source);
  return NextResponse.redirect(url, 303);
}
