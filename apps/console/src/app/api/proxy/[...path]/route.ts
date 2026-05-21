/**
 * Session-aware reverse proxy to ``SHIP_API_URL`` for client components.
 *
 * Browser code calls ``/api/proxy/v1/...``; this handler attaches the
 * httpOnly session bearer and forwards to the backend.
 */

import { NextResponse } from "next/server";

import {
  ApiHttpError,
  ApiUnavailableError,
  apiFetch,
  isApiConfigured,
} from "@/lib/api/client";

type RouteContext = { params: Promise<{ path: string[] }> };

async function forward(request: Request, ctx: RouteContext) {
  if (!isApiConfigured()) {
    return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
  }

  const { path } = await ctx.params;
  const suffix = path.map(encodeURIComponent).join("/");
  const target = `/v1/${suffix}`;
  const url = new URL(request.url);
  const query = url.search;

  let body: unknown = undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const text = await request.text();
    if (text.length > 0) {
      try {
        body = JSON.parse(text);
      } catch {
        return NextResponse.json({ detail: "invalid_json" }, { status: 400 });
      }
    }
  }

  try {
    const data = await apiFetch<unknown>(`${target}${query}`, {
      method: request.method,
      body,
    });
    return NextResponse.json(data);
  } catch (err) {
    if (err instanceof ApiHttpError) {
      return NextResponse.json(
        { detail: err.detail ?? err.message },
        { status: err.status },
      );
    }
    if (err instanceof ApiUnavailableError) {
      return NextResponse.json({ detail: "api_unavailable" }, { status: 503 });
    }
    return NextResponse.json({ detail: "unknown" }, { status: 500 });
  }
}

export const GET = forward;
export const POST = forward;
export const PATCH = forward;
export const PUT = forward;
export const DELETE = forward;
