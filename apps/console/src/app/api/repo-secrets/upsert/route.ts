/**
 * Form handler for the repo-secrets page — create or rotate.
 *
 * Fielded from ``/repos/{id}/secrets`` via a plain
 * ``<form method="POST">`` so the add-secret flow keeps working with
 * zero client JS (important for operators whose CSP / noscript policy
 * blocks inline scripts). We proxy to the backend's admin-gated
 * ``POST /v1/workspaces/{ws}/repos/{repo}/secrets`` endpoint, then
 * bounce back to the page with a banner query string describing the
 * outcome.
 *
 * HTTP mapping:
 * - 200  → ``banner=ok&reason=created|rotated|sync_error`` (branched on
 *          the ``sync_status`` of the returned row).
 * - 401  → redirect to ``/login`` with ``session_expired``.
 * - 403  → ``banner=warn&reason=forbidden`` (member without admin).
 * - 409  → ``banner=warn&reason=missing_install`` (App missing or
 *          suspended).
 * - 422  → the backend's error detail contains the reason string we
 *          use for the banner (e.g. ``bad_name`` / ``too_large``).
 * - 502  → ``banner=error&reason=sync_error`` (GitHub side rejected
 *          before we could persist — row was *not* written in this
 *          branch).
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  upsertRepoSecret,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const name = (form.get("name") ?? "").toString().trim();
  const value = (form.get("value") ?? "").toString();
  const description = (form.get("description") ?? "").toString().trim() || null;

  if (!wsId || !repoId) {
    // No sensible page to bounce to — the dashboard is the least-
    // surprising fallback when we can't tell which repo we meant.
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  const pageUrl = new URL(`/repos/${encodeURIComponent(repoId)}/secrets`, origin);

  if (!isApiConfigured()) {
    return back(pageUrl, "error", "api_unavailable");
  }
  if (!name) return back(pageUrl, "error", "bad_name");
  if (!value) return back(pageUrl, "error", "empty_value");

  try {
    const result = await upsertRepoSecret(wsId, repoId, {
      name,
      value,
      description,
    });
    if (result.sync_status === "synced") {
      const reason =
        result.created_at === result.updated_at ? "created" : "rotated";
      return back(pageUrl, "ok", reason);
    }
    // Row saved, GitHub rejected the sync. The row carries the
    // error message; the page surfaces it inline on the table.
    return back(pageUrl, "warn", "sync_error");
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return back(pageUrl, "error", "api_unavailable");
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      }
      if (err.status === 403) return back(pageUrl, "error", "forbidden");
      if (err.status === 409) {
        return back(pageUrl, "error", "missing_install");
      }
      if (err.status === 422) {
        // FastAPI puts the message in ``detail`` — flatten to a lower
        // string so we can match on a few well-known substrings. The
        // list is intentionally short: anything we haven't taxonomised
        // falls through to ``bad_name`` which is the failure mode the
        // page is best-equipped to surface inline on the form.
        const detailSource = err.detail;
        const detail = (
          typeof detailSource === "string"
            ? detailSource
            : JSON.stringify(detailSource ?? "")
        ).toLowerCase();
        const reason = detail.includes("48kb") || detail.includes("exceed")
          ? "too_large"
          : detail.includes("empty") || detail.includes("required")
            ? "empty_value"
            : "bad_name";
        return back(pageUrl, "error", reason);
      }
      if (err.status === 502) return back(pageUrl, "error", "sync_error");
      return back(pageUrl, "error", `http_${err.status}`);
    }
    return back(pageUrl, "error", "unknown");
  }
}

function back(pageUrl: URL, banner: string, reason: string): NextResponse {
  const url = new URL(pageUrl);
  url.searchParams.set("banner", banner);
  url.searchParams.set("reason", reason);
  return NextResponse.redirect(url, 303);
}
