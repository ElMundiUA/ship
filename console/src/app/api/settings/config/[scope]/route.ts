/**
 * GET / PUT proxy for the per-workspace config registry.
 *
 * Console-side companion to the ``<ConfigScopeCard>`` component:
 * fetch the JSONSchema + current value, render a form, submit the
 * new value, surface errors verbatim. JSON in / JSON out so the
 * card can branch on ``error.code`` (``invalid_value`` /
 * ``unknown_scope``) without parsing a redirect.
 *
 * Workspace id comes via ``?ws=<id>`` query — keeps the URL shape
 * symmetric with the rest of the settings proxies and lets the
 * caller switch workspaces without recompiling the FE.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  getConfigScope,
  isApiConfigured,
  putConfigScope,
} from "@/lib/api/client";


export async function GET(
  request: Request,
  ctx: { params: Promise<{ scope: string }> },
): Promise<Response> {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }
  const { scope } = await ctx.params;
  const url = new URL(request.url);
  const wsId = (url.searchParams.get("ws") || "").trim();
  if (!wsId || !scope) {
    return NextResponse.json(
      { error: "bad_request" },
      { status: 400 },
    );
  }
  try {
    const detail = await getConfigScope(wsId, scope);
    return NextResponse.json(detail);
  } catch (err) {
    return relayError(err);
  }
}


export async function PUT(
  request: Request,
  ctx: { params: Promise<{ scope: string }> },
): Promise<Response> {
  if (!isApiConfigured()) {
    return NextResponse.json({ error: "api_unavailable" }, { status: 503 });
  }
  const { scope } = await ctx.params;
  const url = new URL(request.url);
  const wsId = (url.searchParams.get("ws") || "").trim();
  if (!wsId || !scope) {
    return NextResponse.json(
      { error: "bad_request" },
      { status: 400 },
    );
  }
  let body: { value?: unknown } = {};
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "bad_json" }, { status: 400 });
  }
  if (!("value" in body)) {
    return NextResponse.json(
      { error: "missing_value", message: "Pass {value: ...} in the body." },
      { status: 400 },
    );
  }
  try {
    const result = await putConfigScope(wsId, scope, body.value);
    return NextResponse.json(result);
  } catch (err) {
    return relayError(err);
  }
}


function relayError(err: unknown): Response {
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
    if (err.status === 403) {
      return NextResponse.json({ error: "forbidden" }, { status: 403 });
    }
    if (err.status === 404) {
      const detail = err.detail as { code?: string; message?: string } | null;
      return NextResponse.json(
        {
          error: detail?.code || "not_found",
          message: detail?.message,
        },
        { status: 404 },
      );
    }
    if (err.status === 422) {
      const detail = err.detail as { code?: string; message?: string } | null;
      return NextResponse.json(
        {
          error: detail?.code || "invalid_value",
          message: detail?.message,
        },
        { status: 422 },
      );
    }
    return NextResponse.json(
      { error: `http_${err.status}` },
      { status: err.status },
    );
  }
  return NextResponse.json({ error: "unknown" }, { status: 500 });
}
