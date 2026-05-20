/**
 * Catch-all proxy for backend ``/v1/...`` calls from client components.
 *
 * The browser bundle can't import ``@/lib/api/client`` (it pulls
 * ``next/headers`` for the session cookie, which is server-only). Any
 * ``"use client"`` component that needs to talk to the backend used
 * to either (a) violate the boundary by importing apiFetch anyway —
 * Next.js refuses to bundle, breaks every deploy since ELS-158 — or
 * (b) hand-roll a dedicated ``/api/<feature>`` route for each endpoint.
 *
 * This catch-all keeps the boundary intact without per-endpoint
 * scaffolding: client code calls ``fetch("/api/v1/workspaces/{ws}/...")``
 * with whatever verb/body it wants and the route attaches the session
 * token + forwards to the real backend. Only same-origin browser
 * requests reach this handler; CSRF protection comes from the session
 * cookie being httpOnly + sameSite=lax (set in
 * :func:`setSessionCookie`).
 *
 * Caveats:
 *   - The catch-all forwards verbatim. The backend's own auth /
 *     authorization rules apply; this isn't a "bypass" layer.
 *   - Streaming responses (SSE) aren't handled — for those, write a
 *     dedicated route.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  apiFetch,
  isApiConfigured,
} from "@/lib/api/client";

const ALLOWED_METHODS = new Set([
  "GET",
  "POST",
  "PATCH",
  "PUT",
  "DELETE",
]);

async function handle(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const method = request.method.toUpperCase();
  if (!ALLOWED_METHODS.has(method)) {
    return NextResponse.json(
      { error_code: "method_not_allowed" },
      { status: 405 },
    );
  }
  if (!isApiConfigured()) {
    return NextResponse.json(
      { error_code: "api_unavailable" },
      { status: 503 },
    );
  }

  const url = new URL(request.url);
  const backendPath = `/v1/${path.map(encodeURIComponent).join("/")}${url.search}`;

  let body: unknown = undefined;
  if (method !== "GET" && method !== "DELETE") {
    const ctype = request.headers.get("content-type") || "";
    if (ctype.includes("application/json")) {
      try {
        body = await request.json();
      } catch {
        return NextResponse.json(
          { error_code: "bad_input" },
          { status: 400 },
        );
      }
    }
  }

  try {
    // apiFetch handles the session-token cookie read on the server side
    // and applies it as the Authorization header before forwarding.
    const result = await apiFetch(backendPath, {
      method,
      body: body as Record<string, unknown> | undefined,
    });
    return NextResponse.json(result ?? {});
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { error_code: codeFor(err) },
        { status: err.status },
      );
    }
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json(
        { error_code: "api_unavailable" },
        { status: 503 },
      );
    }
    return NextResponse.json(
      { error_code: "unknown" },
      { status: 500 },
    );
  }
}

function codeFor(err: ApiHttpError): string {
  if (err.status === 401) return "session_expired";
  if (err.status === 403) return "forbidden";
  if (err.status === 404) return "not_found";
  if (err.status === 409) return "state_invalid";
  if (err.status === 422) return "validation_failed";
  return `http_${err.status}`;
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const PUT = handle;
export const DELETE = handle;
