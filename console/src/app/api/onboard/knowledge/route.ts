/**
 * Onboarding step 5 — seed brandbook / code-style / testing docs.
 *
 * The wizard surfaces three checkboxes (one per generator). Whichever ones
 * stay ticked become the `bucket` form values; we forward to
 * `/v1/onboarding/seed-knowledge` and continue to the CLI-token step.
 *
 * Skipping is fine: the user can re-run this from the dashboard later.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  seedKnowledge,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repo = (form.get("repo") ?? "").toString();
  if (!wsId) {
    return NextResponse.redirect(new URL("/onboarding", origin), 303);
  }

  if (form.get("intent") === "skip" || !repo) {
    return advance(origin, wsId);
  }

  if (!isApiConfigured()) {
    return wizardError(origin, wsId, repo, "api_unavailable");
  }

  const buckets = form.getAll("bucket").map((v) => v.toString()).filter(Boolean);
  if (buckets.length === 0) {
    return wizardError(origin, wsId, repo, "missing_selection");
  }

  try {
    const result = await seedKnowledge({
      workspace_id: wsId,
      repo_source: repo,
      bucket_slugs: buckets,
    });
    const url = new URL("/onboarding", origin);
    url.searchParams.set("step", "token");
    url.searchParams.set("ws", wsId);
    if (result.commit_made && result.head_after) {
      url.searchParams.set("seeded", result.docs.length.toString());
    }
    return NextResponse.redirect(url, 303);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return wizardError(origin, wsId, repo, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      const code =
        typeof err.detail === "object" &&
        err.detail !== null &&
        "code" in err.detail
          ? String((err.detail as { code: unknown }).code)
          : `http_${err.status}`;
      return wizardError(origin, wsId, repo, code);
    }
    return wizardError(origin, wsId, repo, "unknown");
  }
}

function advance(origin: string, wsId: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "token");
  url.searchParams.set("ws", wsId);
  return NextResponse.redirect(url, 303);
}

function wizardError(origin: string, wsId: string, repo: string, code: string) {
  const url = new URL("/onboarding", origin);
  url.searchParams.set("step", "knowledge");
  url.searchParams.set("ws", wsId);
  if (repo) url.searchParams.set("repo", repo);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
