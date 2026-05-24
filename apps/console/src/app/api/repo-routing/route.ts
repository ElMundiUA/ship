/**
 * POST /api/repo-routing — set the workspace default repo or bind /
 * unbind a project to a repo (Variant A dispatch routing).
 *
 * Server-action endpoint for the Settings → Connected code routing
 * panel. Plain ``<form>`` POSTs so it works with JS off; forwards to the
 * backend ``/repo-routing`` endpoints and bounces back to the settings
 * page with an ``?error=`` code on failure.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  bindProjectRepo,
  isApiConfigured,
  setDefaultRepo,
  unbindProjectRepo,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const RETURN_TO = "/settings/repositories";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const action = (form.get("action") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString().trim();
  const projectId = (form.get("project_native_id") ?? "").toString().trim();

  if (!wsId || !action) return back(origin, "bad_input");
  if (!isApiConfigured()) return back(origin, "api_unavailable");

  try {
    if (action === "set-default") {
      // Empty repo_id clears the default.
      await setDefaultRepo(wsId, repoId || null);
    } else if (action === "bind") {
      if (!projectId) return back(origin, "bad_input");
      // Empty repo_id ("Use default") clears the binding.
      if (repoId) {
        await bindProjectRepo(wsId, projectId, repoId);
      } else {
        await unbindProjectRepo(wsId, projectId);
      }
    } else if (action === "unbind") {
      if (!projectId) return back(origin, "bad_input");
      await unbindProjectRepo(wsId, projectId);
    } else {
      return back(origin, "bad_input");
    }
  } catch (err) {
    if (err instanceof ApiHttpError && err.status === 401) {
      return NextResponse.redirect(
        new URL("/login?error=session_expired", origin),
        303,
      );
    }
    return back(origin, codeFor(err));
  }

  return NextResponse.redirect(new URL(RETURN_TO, origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL(RETURN_TO, origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}

function codeFor(err: unknown): string {
  if (err instanceof ApiUnavailableError) return "api_unavailable";
  if (err instanceof ApiHttpError) {
    if (err.status === 403) return "forbidden";
    if (err.status === 404) return "not_found";
    if (err.status === 422) return "validation_failed";
    return `http_${err.status}`;
  }
  return "unknown";
}
