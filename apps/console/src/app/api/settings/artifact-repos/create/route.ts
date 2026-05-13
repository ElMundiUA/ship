/**
 * POST /api/settings/artifact-repos/create — register a new artifact repo
 * for a workspace.
 *
 * The settings page renders a small native form so we can keep the page a
 * server component. The form posts `ws`, `kind` (workspace | project),
 * `url`, and an optional `default_branch`. We forward to
 * POST `/v1/workspaces/{id}/artifact-repos`. Validation errors land back on
 * `/settings?error=…` so the page can surface them inline.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  createArtifactRepo,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

const VALID_KINDS = new Set(["workspace", "project"] as const);

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const kindRaw = (form.get("kind") ?? "").toString();
  const url = (form.get("url") ?? "").toString().trim();
  const branchRaw = (form.get("default_branch") ?? "").toString().trim();

  if (!wsId || !VALID_KINDS.has(kindRaw as "workspace" | "project") || url.length === 0) {
    return back(origin, "bad_input");
  }
  if (!isApiConfigured()) {
    return back(origin, "api_unavailable");
  }

  try {
    await createArtifactRepo(wsId, {
      kind: kindRaw as "workspace" | "project",
      url,
      default_branch: branchRaw.length > 0 ? branchRaw : undefined,
    });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, "forbidden");
      if (err.status === 422) return back(origin, "invalid_url");
      return back(origin, `http_${err.status}`);
    }
    return back(origin, "unknown");
  }

  return NextResponse.redirect(new URL("/settings", origin), 303);
}

function back(origin: string, code: string) {
  const url = new URL("/settings", origin);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
