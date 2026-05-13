/**
 * GET /api/settings/config — list every registered config scope.
 *
 * Used by the settings landing page to render one ``<ConfigScopeCard>``
 * per scope without hard-coding the list. Adding a new
 * :class:`backend.app.services.config_registry.ConfigScope` row picks
 * up here automatically.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  isApiConfigured,
  listConfigScopes,
} from "@/lib/api/client";


export async function GET(request: Request): Promise<Response> {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }
  const url = new URL(request.url);
  const wsId = (url.searchParams.get("ws") || "").trim();
  if (!wsId) {
    return NextResponse.json({ error: "ws_required" }, { status: 400 });
  }
  try {
    const rows = await listConfigScopes(wsId);
    return NextResponse.json({ scopes: rows });
  } catch (err) {
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ error: "api_unavailable" }, { status: 502 });
    }
    if (err instanceof ApiHttpError) {
      if (err.status === 401) {
        return NextResponse.json(
          { error: "session_expired" },
          { status: 401 },
        );
      }
      return NextResponse.json(
        { error: `http_${err.status}` },
        { status: err.status },
      );
    }
    return NextResponse.json({ error: "unknown" }, { status: 500 });
  }
}
