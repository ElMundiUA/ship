/**
 * POST /api/settings/workspace/rename — rename the current workspace.
 *
 * Only ``name`` is editable; ``slug`` is immutable post-create (the
 * URL handle, audit-log keys, and a few client-side caches lean on
 * stability). The form posts ``ws`` and ``name``; we forward to
 * ``PATCH /v1/workspaces/{ws}`` and bounce back to the same tab so
 * the operator sees the updated row immediately.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  updateWorkspace,
} from "@/lib/api/client";
import { resolveOrigin } from "@/lib/api/origin";

export async function POST(request: Request) {
  const origin = resolveOrigin(request);
  const form = await request.formData();
  const wsId = (form.get("ws") ?? "").toString();
  const name = (form.get("name") ?? "").toString().trim();
  if (!wsId || !name) return back(origin, wsId, "bad_input");
  if (name.length > 200) return back(origin, wsId, "bad_input");
  if (!isApiConfigured()) return back(origin, wsId, "api_unavailable");

  try {
    await updateWorkspace(wsId, { name });
  } catch (err) {
    if (err instanceof ApiUnavailableError) return back(origin, wsId, "api_unavailable");
    if (err instanceof ApiHttpError) {
      if (err.status === 401)
        return NextResponse.redirect(new URL("/login?error=session_expired", origin), 303);
      if (err.status === 403) return back(origin, wsId, "forbidden");
      if (err.status === 404) return back(origin, wsId, "not_found");
      return back(origin, wsId, `http_${err.status}`);
    }
    return back(origin, wsId, "unknown");
  }

  const url = new URL("/settings/workspaces", origin);
  url.searchParams.set("ws", wsId);
  url.searchParams.set("renamed", "1");
  return NextResponse.redirect(url, 303);
}

function back(origin: string, wsId: string, code: string) {
  const url = new URL("/settings/workspaces", origin);
  if (wsId) url.searchParams.set("ws", wsId);
  url.searchParams.set("error", code);
  return NextResponse.redirect(url, 303);
}
