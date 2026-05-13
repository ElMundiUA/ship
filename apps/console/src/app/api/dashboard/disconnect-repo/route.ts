/**
 * Dashboard form handler — Disconnect Ship from a repo (B6).
 *
 * Cascades on the backend: kills the ``WorkspaceRepo`` row, every
 * pipeline bound to it, and every run under those pipelines. Doesn't
 * touch github.com — removing the App from the repo's "selected
 * repositories" list stays a user-initiated flow on github.com, and
 * the workflow YAMLs the install PR added belong to the customer
 * repo now.
 *
 * We require a ``confirm`` field from the modal so a stray click can't
 * nuke someone's state — the backend route itself doesn't enforce that
 * (keeps it machine-consumable), but the UI always opens the modal.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  disconnectRepo,
  isApiConfigured,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const repoId = (form.get("repo_id") ?? "").toString();
  const confirm = (form.get("confirm") ?? "").toString();

  if (!wsId || !repoId) {
    return NextResponse.redirect(new URL("/", origin), 303);
  }
  if (confirm !== "disconnect") {
    return back(origin, wsId, "disconnect_confirm_missing");
  }
  if (!isApiConfigured()) {
    return back(origin, wsId, "api_unavailable");
  }

  try {
    const result = await disconnectRepo(wsId, repoId);
    const detail = `${result.full_name} · ${result.deleted_pipelines} lane(s), ${result.deleted_runs} run(s) removed`;
    return back(origin, wsId, "disconnected", detail);
  } catch (err) {
    if (err instanceof ApiUnavailableError)
      return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(
          new URL("/login?error=session_expired", origin),
          303,
        );
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "disconnect_missing");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }
}

function back(origin: string, wsId: string, reason: string, detail?: string) {
  const url = new URL("/", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("disconnected", "1");
  url.searchParams.set("reason", reason);
  if (detail) url.searchParams.set("detail", detail);
  return NextResponse.redirect(url, 303);
}
