/**
 * Form handler for the repo-secrets page — delete.
 *
 * Posts ``ws``, ``repo_id``, and ``secret_id``. We proxy to the
 * backend's admin-gated ``DELETE /v1/workspaces/{ws}/repos/{repo}/secrets/{id}``
 * and bounce back to the secrets page. Because the backend deletes
 * on GitHub first and in DB second, a 502 here means the secret is
 * still live on GitHub — the banner surfaces that so the operator
 * knows to retry rather than assuming it's gone.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  deleteRepoSecret,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const secretId = (form.get("secret_id") ?? "").toString();

  if (!wsId || !repoId || !secretId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  const pageUrl = new URL(`/repos/${encodeURIComponent(repoId)}/secrets`, origin);
  if (!isApiConfigured()) return back(pageUrl, "error", "api_unavailable");

  try {
    await deleteRepoSecret(wsId, repoId, secretId);
    return back(pageUrl, "ok", "deleted");
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
      if (err.status === 404) return back(pageUrl, "error", "not_found");
      if (err.status === 409) {
        return back(pageUrl, "error", "missing_install");
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
