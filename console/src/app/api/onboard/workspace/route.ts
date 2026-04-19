/**
 * Onboarding step 1 — create a workspace.
 *
 * Native form POST so the browser handles `Set-Cookie` + 303 redirects without
 * the cookie-drop foot-gun we hit with Server Actions in standalone mode.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createWorkspace,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const SLUG_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  if (!isApiConfigured()) {
    return wizardError(origin, "workspace", "api_unavailable", { name: "", slug: "" });
  }

  const form = await request.formData();
  const name = (form.get("name") ?? "").toString().trim();
  const slug = (form.get("slug") ?? "").toString().trim();
  const repo = (form.get("repo") ?? "").toString().trim();

  if (!name || !slug) {
    return wizardError(origin, "workspace", "missing_fields", { name, slug });
  }
  if (!SLUG_RE.test(slug)) {
    return wizardError(origin, "workspace", "bad_slug", { name, slug });
  }

  try {
    const ws = await createWorkspace({ name, slug });
    const next = new URL("/onboarding", origin);
    // After workspace creation, push the user into the GitHub App install
    // (the WOW-onboarding hero step). They can still skip to workflows or
    // tracker from there. Operators with no repo paste survived this far
    // by clicking "Use a demo repo" and we treat them the same — the
    // GitHub install is independent of the inspected repo.
    next.searchParams.set("step", "github");
    next.searchParams.set("ws", ws.id);
    if (repo) next.searchParams.set("repo", repo);
    return NextResponse.redirect(next, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return wizardError(origin, "workspace", "api_unavailable", { name, slug });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      }
      if (err.status === 409) {
        return wizardError(origin, "workspace", "slug_taken", { name, slug });
      }
      if (err.status === 422) {
        return wizardError(origin, "workspace", "bad_slug", { name, slug });
      }
      return wizardError(origin, "workspace", `http_${err.status}`, { name, slug });
    }
    return wizardError(origin, "workspace", "unknown", { name, slug });
  }
}

function wizardError(
  origin: string,
  step: string,
  code: string,
  fields: { name: string; slug: string },
) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", step);
  url.searchParams.set("error", code);
  if (fields.name) url.searchParams.set("name", fields.name);
  if (fields.slug) url.searchParams.set("slug", fields.slug);
  return NextResponse.redirect(url, 303);
}
